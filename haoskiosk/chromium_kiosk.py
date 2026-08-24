"""-------------------------------------------------------------------------------
# Add-on: HAOS Kiosk Display (haoskiosk)
# File: chromium_kiosk.py
# Version: 1.4.28
# Copyright Jeff Kosowsky
# Date: August 2026

Drives a regular (non-forked) Chromium browser in kiosk mode via the Chrome
DevTools Protocol (CDP), replacing the old Luakit-based 'userconf.lua'.

Unlike Luakit, Chromium has no in-process Lua scripting hook, so every behavior
that userconf.lua used to implement natively is instead driven externally
over CDP once Chromium is launched with '--remote-debugging-port':
  - Auto-login to Home Assistant (JS injected via Runtime.evaluate on the auth page)
  - HA sidebar/theme localStorage settings (JS injected once per dashboard load)
  - Dark/light mode forced via CDP Emulation.setEmulatedMedia (matches the
    'prefers-color-scheme' media query without Chromium's page-recoloring
    "Force Dark" heuristic, which would otherwise visually distort the HA UI)
  - Unhandled-rejection suppression + HA websocket recovery watchdog, injected
    via Page.addScriptToEvaluateOnNewDocument so they run on every navigation
  - Voice Satellite auto-start ('voice_satellite' option): microphone access is
    pre-granted over CDP so the browser never prompts or blocks on a missing
    user gesture, plain-http HA instances are treated as a secure origin so
    getUserMedia works without HTTPS, and if Voice Satellite's floating
    'tap to start' button still appears it is tapped automatically via CDP
    input events - so voice comes up hands-free on boot instead of requiring
    a manual tap after every restart/reload
  - Periodic browser refresh (native Page.reload, with periodic hard/cache-busting reload)
  - Restart Chromium after consecutive main-document load failures, falling back
    from hardware (EGL) to software (SwiftShader) GL if Chromium fails to start
  - Zoom level is applied via '--force-device-scale-factor' at launch (global,
    like Luakit's webview.zoom_level) rather than per-page CSS

Navigating to a new URL (REST 'launch_url', gesture-triggered URL launches) is
done by sending 'Page.navigate' to the single running Chromium tab rather than
spawning a second browser process, so there's no need for Luakit's
'unique_instance' patch to reuse the current tab.
#-------------------------------------------------------------------------------"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import shutil
import time
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout  # type: ignore[import-not-found] # pylint: disable=import-error

from cdp_client import CDPConnection, DEFAULT_CDP_HOST, DEFAULT_CDP_PORT

logger = logging.getLogger(__name__)

__version__ = "1.4.28"

CHROMIUM_BIN = "chromium"  # Resolved via PATH
PROFILE_DIR = "/root/.config/chromium-kiosk"

HARD_RELOAD_FREQ = 10   # Every Nth periodic refresh also bypasses cache (mirrors old userconf.lua)
MAX_LOAD_FAILURES = 5   # Consecutive main-document load failures before restarting Chromium
CDP_READY_TIMEOUT = 20  # Seconds to wait for Chromium's CDP endpoint to come up
GRACEFUL_STOP_TIMEOUT = 5  # Seconds to wait for SIGTERM before SIGKILL
DBUS_SEND_TIMEOUT = 5  # Seconds to wait for a dbus-send call (e.g. Onboard hide) before killing it
MAX_RESTARTS_PER_WINDOW = 5   # Give up restarting (let the container exit) after this many restarts...
RESTART_WINDOW_SECONDS = 180  # ...within this many seconds - avoids a tight crash-restart loop
HEALTH_CHECK_INTERVAL = 3          # Seconds between CDP reachability polls
HEALTH_CHECK_HTTP_TIMEOUT = 2      # Seconds to wait for each poll
HEALTH_CHECK_FAILURE_THRESHOLD = 2  # Consecutive failed polls before treating Chromium as down
HARDWARE_GL_RETRY_INTERVAL = 1800  # Seconds of stable software-GL operation before retrying hardware GL
HARDWARE_GL_RETRY_CHECK_INTERVAL = 60  # How often to check whether that cooldown has elapsed
DPMS_WATCH_INTERVAL = 5   # Seconds between 'xset -q' polls for screen on/off (see _dpms_watch_loop)
DPMS_QUERY_TIMEOUT = 3    # Seconds to wait for 'xset -q' before giving up on that poll
# Voice Satellite auto-start (see _voice_satellite_autostart): Voice Satellite's own engine
# tries to auto-start once a satellite entity is assigned, but if the browser blocks mic
# capture for lack of a user gesture it parks behind a floating 'tap to start' button.
# Poll this often / at most this long after each page load for that button to appear,
# and tap it over CDP so the user never has to.
VS_AUTOSTART_POLL_INTERVAL = 1.0  # Seconds between polls for the floating start button
VS_AUTOSTART_MAX_WAIT = 45        # Total seconds to keep watching after each page load
VS_AUTOSTART_MAX_TAPS = 3         # Give up tapping after this many failed attempts
VS_AUTOSTART_RECHECK_DELAY = 3.0  # Seconds to wait after a tap before verifying it worked


def parse_dpms_monitor_on(xset_q_output: str) -> bool | None:
    """Parse 'xset -q' output for DPMS monitor state. Returns True if on, False if
    off/standby/suspend, or None if the expected 'Monitor is ...' line isn't present at all
    (DPMS disabled, or unexpected xset output - callers should treat that as 'unknown', not
    'off'). Pulled out as a pure function so the parsing itself is unit-testable without X11."""
    for line in xset_q_output.splitlines():
        line = line.strip()
        if line.startswith("Monitor is "):
            return line == "Monitor is On"
    return None


def _single_quote_escape(s: str) -> str:
    """Escape a string for safe embedding inside a single-quoted JS string literal."""
    if not s:
        return s
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "\\'")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    return s


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() == "true"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


class ChromiumKiosk:
    """Launches and drives a kiosk Chromium instance over CDP."""

    def __init__(self) -> None:
        # --- Configuration (mirrors userconf.lua's env var handling) ---
        self.ha_username = os.getenv("HA_USERNAME", "")
        self.ha_password = os.getenv("HA_PASSWORD", "")

        ha_url = (os.getenv("HA_URL") or "http://localhost:8123").rstrip("/")
        parsed = urlsplit(ha_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            logger.warning("Invalid HA_URL value: '%s'; defaulting to http://localhost:8123", ha_url)
            ha_url = "http://localhost:8123"
            parsed = urlsplit(ha_url)
        self.ha_url = ha_url
        self.ha_url_base = f"{parsed.scheme}://{parsed.netloc}"

        dashboard = (os.getenv("HA_DASHBOARD") or "").strip("/")
        self.initial_url = f"{self.ha_url}/{dashboard}".rstrip("/") if dashboard else self.ha_url

        self.login_delay = max(_env_float("LOGIN_DELAY", 1.0), 0.1)

        raw_zoom = _env_float("ZOOM_LEVEL", 100)
        self.zoom_level = min(max(raw_zoom, 25), 500)
        if self.zoom_level != raw_zoom:
            logger.warning("ZOOM_LEVEL value %s out of range; clamped to %s", raw_zoom, self.zoom_level)

        self.browser_refresh = max(_env_float("BROWSER_REFRESH", 600), 0)
        self.dark_mode = _env_bool("DARK_MODE", True)
        self.onscreen_keyboard = _env_bool("ONSCREEN_KEYBOARD", False)
        # See _dpms_watch_loop: freezes the page (stops all rendering/compositing/timers) while
        # the physical screen is DPMS-blanked, since nobody can see it either way. Unlike a
        # transient screensaver overlay, "screen off" here can last minutes to hours, so unlike
        # freezing being a bad idea for that shorter-lived case, the CPU/GPU/heat savings are
        # worth a brief reload when the screen comes back on (see _dpms_watch_loop's docstring).
        self.pause_on_screen_off = _env_bool("PAUSE_ON_SCREEN_OFF", True)

        raw_sidebar = (os.getenv("HA_SIDEBAR") or "").strip().lower()
        valid_sidebars = {"full": "", "none": '"always_hidden"', "narrow": '"auto"', "": ""}
        self.sidebar_js_value = valid_sidebars.get(raw_sidebar, "")
        if raw_sidebar and raw_sidebar not in valid_sidebars:
            logger.warning("Invalid HA_SIDEBAR value: '%s'; defaulting to unset", raw_sidebar)

        theme = (os.getenv("HA_THEME") or "").strip()
        if theme and theme[0] not in ('"', "'", "{"):
            theme = f'"{theme}"'
        self.theme_js_value = theme

        # BCP-47 locale (e.g. "es-ES", "fr", "pt-BR") for both Chromium itself (--lang, and thus
        # which locale .pak file it loads) and the page content Home Assistant sees (CDP locale
        # override + Accept-Language, so HA's own "auto" frontend-language detection - which reads
        # navigator.language/Accept-Language whenever the user hasn't picked a language in their HA
        # profile - matches rather than silently falling back to Chromium's compiled-in en-US).
        # Accept a common user typo (underscore instead of hyphen, e.g. "es_ES") rather than
        # silently failing to apply the requested language.
        self.browser_language = (os.getenv("BROWSER_LANGUAGE") or "").strip().replace("_", "-")

        # Voice Satellite auto-start: pre-grant mic access, treat plain-http HA as a secure
        # origin, and tap Voice Satellite's floating start button if it ever shows up - so
        # the wake-word engine comes up on boot without a manual tap (mirrors what the
        # official Android Kiosk Satellite app does natively, within browser limits).
        self.voice_satellite = _env_bool("VOICE_SATELLITE", False)

        logger.info(
            "ChromiumKiosk config: URL=%s DARK_MODE=%s SIDEBAR=%s THEME=%s LANGUAGE=%s LOGIN_DELAY=%.1f "
            "ZOOM_LEVEL=%d BROWSER_REFRESH=%d ONSCREEN_KEYBOARD=%s VOICE_SATELLITE=%s",
            self.initial_url, self.dark_mode, raw_sidebar, theme or "(none)",
            self.browser_language or "(default)",
            self.login_delay, self.zoom_level, self.browser_refresh, self.onscreen_keyboard,
            self.voice_satellite,
        )
        if self.voice_satellite and (
            self.pause_on_screen_off
            or _env_float("SCREEN_TIMEOUT", 0) > 0
        ):
            logger.warning(
                "VOICE_SATELLITE is enabled but the page freezes/stops when the screen blanks "
                "(pause_on_screen_off and/or screen_timeout) - the microphone and wake-word "
                "engine stop with it. Keep the screen always on (screen_timeout=0, "
                "pause_on_screen_off=false) for hands-free voice."
            )

        # --- Runtime state ---
        self.proc: asyncio.subprocess.Process | None = None
        self.conn: CDPConnection | None = None
        self._current_url = self.initial_url
        self._settings_applied = False
        self._consecutive_failures = 0
        self._refresh_deadline = 0.0
        self._hard_reload_count = 0
        self._refresh_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._health_check_task: asyncio.Task[None] | None = None
        self._hardware_retry_task: asyncio.Task[None] | None = None
        self._dpms_watch_task: asyncio.Task[None] | None = None
        self._page_frozen = False  # Mirrors the page's actual Page.setWebLifecycleState - see _dpms_watch_loop
        # Fire-and-forget tasks spawned via self._spawn() (restarts, auto-login, on-page-loaded
        # hooks) - kept here so nothing relies solely on asyncio.create_task()'s internal weak
        # reference, which per asyncio's own docs can let an in-flight task be garbage-collected.
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._restart_lock = asyncio.Lock()
        self._restart_timestamps: list[float] = []
        self._force_software_gl = False  # Set once hardware GL is observed to crash post-startup
        self._software_gl_since: float | None = None  # When _force_software_gl was last set
        self._active_gl_mode: str | None = None
        self._stopping = False
        # Set when the restart-rate-limiter gives up permanently (see _restart_browser). rest_server.py
        # waits on this so the REST server process itself exits promptly - run.sh in turn just waits
        # on that process, rather than independently polling for a browser process by name.
        self.gave_up = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Launch Chromium and establish the CDP control session."""
        await self._launch_process()
        await self._connect_cdp()
        self._watchdog_task = asyncio.create_task(self._watch_process_exit(self.proc))
        # Belt-and-suspenders: proc.wait() *should* unblock as soon as Chromium exits, but relies
        # on asyncio's child-watcher/SIGCHLD machinery, which has proven unreliable in at least
        # one deployment environment (a crash went undetected until run.sh's own ~15s pgrep-based
        # timeout gave up on the whole add-on). This polls CDP reachability directly instead, so
        # detection doesn't depend on that machinery at all - it also catches a hung-but-still-
        # alive process, which proc.wait() would never notice.
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        # A single hardware-GL crash forces software rendering for the rest of the process's
        # life otherwise - fine for stability, but SwiftShader is pure software rasterization,
        # so anything animation-heavy (canvas/WebGL dashboard cards in particular) can end up
        # running at a couple of frames per second even though the underlying hardware crash was
        # a one-off transient issue. Periodically give hardware GL another chance instead of
        # sticking with software forever.
        self._hardware_retry_task = asyncio.create_task(self._hardware_retry_loop())
        if self.pause_on_screen_off:
            self._dpms_watch_task = asyncio.create_task(self._dpms_watch_loop())
        logger.info("ChromiumKiosk started: %s", self._current_url)

        if self.browser_refresh > 0:
            self._reset_refresh_timer()
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        """Create a fire-and-forget task while keeping a strong reference to it. Per asyncio's own
        docs, create_task() only stores a *weak* reference internally - a task with no other
        referrer can be garbage-collected mid-execution at any await point. Several of ours (a
        full browser restart in particular) run for multiple seconds across several awaits, which
        is exactly the scenario that warning is about."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)
        return task

    def _on_background_task_done(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Background task failed", exc_info=exc)

    async def stop(self) -> None:
        """Gracefully tear down Chromium and the CDP session."""
        self._stopping = True
        tasks = [t for t in (
            self._refresh_task, self._watchdog_task, self._health_check_task,
            self._hardware_retry_task, self._stderr_task, self._dpms_watch_task,
        ) if t is not None]
        tasks += list(self._background_tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            # Make sure each task's own cleanup (e.g. _health_check_loop's ClientSession,
            # _stream_stderr's reader) actually finishes before we tear down the rest of our
            # state below, rather than letting it unwind concurrently with/after it.
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.conn:
            with suppress(Exception):
                await self.conn.close()
            self.conn = None
        await self._kill_process()
        with suppress(Exception):
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)

    async def wait(self) -> int:
        """Wait for the Chromium process to exit and return its exit code."""
        if self.proc is None:
            return -1
        return await self.proc.wait()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def get_gpu_info(self) -> dict[str, Any] | None:
        """
        Query Chromium's real, authoritative GPU feature status via CDP's SystemInfo.getInfo -
        the exact same data chrome://gpu itself reads from. Our own hardware/software GL-mode
        tracking only knows which launch flags we used and whether the process stayed up; it
        can't tell us whether GPU compositing/rasterization/WebGL are *actually* active, which is
        the only way to be sure the render path is really accelerated end to end rather than
        silently falling back to software for some unrelated reason (driver quirk, missing
        extension, etc.) despite hardware-mode flags and a healthy process.
        """
        if self.conn is None:
            return None
        # SystemInfo.getInfo is only available on the browser-level CDP target, not the page
        # target self.conn is connected to ("... is only supported on the browser target") - so
        # this needs its own short-lived connection rather than reusing self.conn.
        browser_conn: CDPConnection | None = None
        try:
            browser_conn = await CDPConnection.connect_browser(DEFAULT_CDP_HOST, DEFAULT_CDP_PORT)
            result = await browser_conn.send("SystemInfo.getInfo", timeout=10.0)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[get_gpu_info] SystemInfo.getInfo failed: %s", e)
            return None
        finally:
            if browser_conn is not None:
                with suppress(Exception):
                    await browser_conn.close()
        gpu = result.get("gpu", {})
        return {
            "feature_status": gpu.get("featureStatus", {}),
            "devices": [
                {"vendor": d.get("vendorString"), "device": d.get("deviceString"),
                 "driver_vendor": d.get("driverVendor"), "driver_version": d.get("driverVersion")}
                for d in gpu.get("devices", [])
            ],
            "gl_renderer": gpu.get("auxAttributes", {}).get("glRenderer"),
            "gl_vendor": gpu.get("auxAttributes", {}).get("glVendor"),
        }

    # ------------------------------------------------------------------ #
    # Public control API (used by rest_server.py / gestures)
    # ------------------------------------------------------------------ #
    async def navigate(self, url: str) -> bool:
        """Navigate the single kiosk tab to 'url' (replaces spawning a second browser instance)."""
        if self.conn is None:
            logger.error("[navigate] No active CDP connection")
            return False
        try:
            await self.conn.send("Page.navigate", {"url": url})
        except Exception as e:  # pylint: disable=broad-except
            logger.error("[navigate] Failed to navigate to %s: %s", url, e)
            return False
        self._current_url = url
        self._settings_applied = False
        self._reset_refresh_timer()
        return True

    async def reload(self, ignore_cache: bool = False) -> bool:
        """Reload the current page."""
        if self.conn is None:
            logger.error("[reload] No active CDP connection")
            return False
        try:
            await self.conn.send("Page.reload", {"ignoreCache": ignore_cache})
        except Exception as e:  # pylint: disable=broad-except
            logger.error("[reload] Failed to reload: %s", e)
            return False
        self._reset_refresh_timer()
        return True

    # ------------------------------------------------------------------ #
    # Process management
    # ------------------------------------------------------------------ #
    def _build_args(self, gl_mode: str) -> list[str]:
        args = [
            f"--app={self._current_url}",
            "--kiosk",
            "--no-sandbox",                 # Required: Chromium refuses to run sandboxed as root in-container
            "--disable-dev-shm-usage",      # Avoid renderer crashes from a small /dev/shm in containers
            "--disable-setuid-sandbox",
            "--noerrdialogs",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--disable-translate",
            "--disable-features=TranslateUI",
            "--overscroll-history-navigation=0",
            "--disable-pinch",
            "--autoplay-policy=no-user-gesture-required",
            "--check-for-update-interval=31536000",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-component-update",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-crash-reporter",
            "--password-store=basic",
            "--use-mock-keychain",
            "--start-fullscreen",
            "--window-position=0,0",
            f"--force-device-scale-factor={self.zoom_level / 100:.4f}",
            f"--user-data-dir={PROFILE_DIR}",
            "--remote-debugging-port=" + str(DEFAULT_CDP_PORT),
            "--remote-debugging-address=" + DEFAULT_CDP_HOST,
            "--remote-allow-origins=*",
            # We always run under a plain Xorg session (never Wayland/headless), so pin the
            # Ozone backend explicitly rather than relying on Chromium's auto-detection - on
            # some boards/builds that auto-detection has been the actual cause of GPU-process
            # init failures rather than the GPU driver itself.
            "--ozone-platform=x11",
            # Chromium's GPU process has its own sandbox layer, separate from --no-sandbox,
            # that can fail to initialize under the more restricted namespaces/seccomp profile
            # containers typically run with - even with SYS_ADMIN granted. Disabling it (we're
            # already unsandboxed overall) avoids GPU-process-init crashes caused by that layer.
            "--disable-gpu-sandbox",
            # Chromium's internal GPU allow/block-list is tuned for common desktop/laptop GPUs
            # and can misidentify or blanket-reject less common driver/board combos (e.g.
            # Raspberry Pi's V3D), forcing an unwanted software fallback or GPU-process crash
            # loop. Both flag spellings are kept for cross-version Chromium compatibility.
            "--ignore-gpu-blocklist",
            "--ignore-gpu-blacklist",
            # --- Performance: a kiosk window is never "in the background" from the user's
            # perspective, but Chromium's power-saving heuristics don't know that - an unfocused
            # or occluded window (easy to end up with under a bare window manager with no
            # decorations) gets its JS timers/rAF throttled and rendering deprioritized, which is
            # the single biggest cause of a kiosk dashboard feeling laggy/stale rather than an
            # actual rendering bottleneck. Disable all of that unconditionally.
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
            "--disable-hang-monitor",
            # This is always a single trusted origin (the user's own HA instance) in a single
            # --app window, so the extra renderer processes/IPC overhead Site Isolation adds for
            # cross-origin protection buys nothing here and only costs memory/CPU - meaningful on
            # the kind of constrained boards this add-on typically runs on.
            "--disable-site-isolation-trials",
            "--renderer-process-limit=1",
            # Skia's GPU-accelerated Canvas2D backend miscompiles some composite operations on the
            # kind of GPUs these boards ship (verified on a Raspberry Pi's V3D through ANGLE):
            # a path built from zero-radius arcs plus a ~358-degree sweep, filled under
            # globalCompositeOperation='destination-out', erases far more than the path covers.
            # Real-world victim: the Material You panel's colour disk, which builds its wheel by
            # accumulating 360 two-degree wedges cut out exactly that way - it renders as a
            # handful of stray radial lines instead of a colour wheel. Confirmed by reading the
            # canvas back with toDataURL (the pixels are already wrong before compositing) and by
            # re-running the same drawing code on the device under different flags: broken with
            # --use-gl=angle alone, byte-identical to the software renderer once Canvas2D
            # acceleration is off. Only Canvas2D goes back to the CPU here; GPU compositing,
            # rasterization and WebGL all stay hardware-accelerated.
            "--disable-accelerated-2d-canvas",
        ]
        if self.voice_satellite and urlsplit(self.ha_url_base).scheme == "http":
            # Voice Satellite needs getUserMedia, which browsers only expose in secure contexts.
            # On a plain-http HA instance that would silently kill the wake-word engine - the
            # same problem the official Android Kiosk Satellite app solves with its loopback
            # proxy. This is the Chromium-blessed equivalent for a kiosk: treat exactly this
            # one origin as secure. Only ever our own single trusted HA origin, so the "unsafe"
            # in the flag name buys no extra exposure beyond what the kiosk already is.
            args.append(f"--unsafely-treat-insecure-origin-as-secure={self.ha_url_base}")
        if self.browser_language:
            # Selects which locale .pak file Chromium loads (its own UI strings, spellchecker,
            # etc). Also the initial signal Chromium uses to seed Accept-Language/navigator.language
            # before our CDP-level overrides (Emulation.setLocaleOverride + Network.setExtraHTTPHeaders,
            # applied in _connect_cdp) take effect on every page load.
            args.append(f"--lang={self.browser_language}")
        if self.onscreen_keyboard:
            # Onboard's auto-show (pop the keyboard up when a text field is focused, the only way
            # it ever appears on its own) works by watching AT-SPI for a focused editable node.
            # Chromium registers itself on the AT-SPI bus regardless, but *without* this flag it
            # only ever exposes its own browser UI - the entire renderer-side tree, i.e. every
            # text field on the page, is simply absent from the accessibility tree, so nothing an
            # AT can observe ever changes when you tap an input in the dashboard and Onboard
            # stays hidden. Verified on a real device: with the flag, Onboard logs the focused
            # node as role=ENTRY state=[EDITABLE, FOCUSED, ...] and auto-shows immediately;
            # without it, walking the AT-SPI tree from Chromium's application node yields only a
            # [frame] whose children are all null. This is what Luakit gave us for free upstream
            # (GTK apps expose their a11y tree via the ATK bridge by default) and what silently
            # went missing in the switch to Chromium.
            # Only set when the onscreen keyboard is actually enabled: maintaining the renderer
            # a11y tree costs real CPU/memory on every DOM update, which is not worth paying on a
            # constrained board for a kiosk that has no use for it.
            args.append("--force-renderer-accessibility")
        if gl_mode == "software":
            args += ["--use-gl=angle", "--use-angle=swiftshader-webgl", "--disable-gpu-compositing"]
        else:
            # --use-gl=egl requests the old direct-EGL path *without* going through ANGLE
            # (internally: gl=egl-gles2,angle=none). Confirmed via captured Chromium stderr on a
            # real device that this build's GPU process refuses that combination outright:
            #   "Requested GL implementation (gl=egl-gles2,angle=none) not found in allowed
            #    implementations: [(gl=egl-angle,angle=default)]" -> "Exiting GPU process due to
            #    errors during initialization"
            # Modern Chromium on Linux only supports going through ANGLE ("ANGLE everywhere");
            # the GPU process was exiting immediately on every single launch, silently leaving
            # every GPU feature (compositing, rasterization, WebGL, ...) disabled/software for
            # the rest of that run - it never crashed the browser or failed CDP, so nothing
            # caught it except reading Chromium's own stderr. --use-gl=angle with no explicit
            # --use-angle= (letting it auto-select, i.e. angle=default) is what the error message
            # itself says is accepted.
            args += ["--use-gl=angle", "--enable-gpu-rasterization", "--enable-zero-copy"]
        return args

    def _chromium_env(self) -> dict[str, str]:
        """Environment for the Chromium subprocess. run.sh sets LANG from KEYBOARD_LAYOUT (a
        keyboard code, e.g. 'us'/'de', not a real locale) purely for xkb's own benefit, so when a
        real BROWSER_LANGUAGE is configured, override LANGUAGE here (gettext/ICU consult it before
        LANG) so nothing in Chromium picks up that bogus inherited value instead."""
        env = os.environ.copy()
        if self.browser_language:
            env["LANGUAGE"] = self.browser_language
        return env

    async def _launch_process(self, force_software: bool | None = None) -> None:
        """'force_software' overrides self._force_software_gl for just this one launch attempt
        (used by the hardware-GL retry path, which needs to actually *try* hardware without
        touching the persistent flag until the outcome of this specific attempt is known - see
        _restart_browser's clear_software_gl handling). None (the default) uses the persistent
        flag as-is."""
        use_force_software = self._force_software_gl if force_software is None else force_software
        gl_modes = ("software",) if use_force_software else ("hardware", "software")
        for gl_mode in gl_modes:
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)  # Always start from a fresh profile (no session restore)
            os.makedirs(PROFILE_DIR, exist_ok=True)
            self._seed_profile_preferences()

            args = self._build_args(gl_mode)
            logger.info("Launching Chromium (%s GL): %s %s", gl_mode, CHROMIUM_BIN, " ".join(args))
            self.proc = await asyncio.create_subprocess_exec(
                CHROMIUM_BIN, *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=self._chromium_env(),
            )
            # Chromium's own diagnostics (EGL/GBM/Mesa/GPU-process errors in particular) go to
            # stderr. A GPU-process init failure doesn't necessarily crash the whole browser or
            # even show up as a CDP-level problem - Chromium just silently runs with GPU features
            # disabled/software - so without this we'd only ever know THAT it failed (via
            # get_gpu_info), never WHY.
            # Uses _spawn() (not a bare create_task()) because this loop can reassign
            # self._stderr_task on its next iteration (hardware attempt failed, retrying with
            # software) before the previous attempt's reader task has necessarily finished
            # draining/exiting - at that point the old task would otherwise have no reference
            # left anywhere, the exact GC-mid-execution hazard _spawn() exists to close.
            self._stderr_task = self._spawn(self._stream_stderr(self.proc))

            if await self._wait_for_cdp_ready(CDP_READY_TIMEOUT):
                logger.info("Chromium ready (%s GL, pid=%d)", gl_mode, self.proc.pid)
                self._active_gl_mode = gl_mode
                return

            logger.warning("Chromium failed to become ready with %s GL rendering", gl_mode)
            await self._kill_process()

        raise RuntimeError("Chromium failed to start with both hardware and software GL rendering")

    _GPU_LOG_KEYWORDS = ("gpu", "egl", "gbm", "gl error", "glerror", "vulkan", "angle", "mesa", "dri", "v3d", "vc4")

    async def _stream_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Log Chromium's stderr lines that look GPU/graphics-related at WARNING (so they show up
        in the add-on's normal log automatically); everything else at DEBUG to avoid flooding it
        with Chromium's usual unrelated noise."""
        if proc.stderr is None:
            return
        try:
            async for raw_line in proc.stderr:
                line = raw_line.decode(errors="replace").rstrip()
                if not line:
                    continue
                if any(kw in line.lower() for kw in self._GPU_LOG_KEYWORDS):
                    logger.warning("[chromium stderr] %s", line)
                else:
                    logger.debug("[chromium stderr] %s", line)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pylint: disable=broad-except
            logger.debug("[_stream_stderr] Reader stopped: %s", e)

    async def _wait_for_cdp_ready(self, timeout: float) -> bool:
        url = f"http://{DEFAULT_CDP_HOST}:{DEFAULT_CDP_PORT}/json/version"
        deadline = time.monotonic() + timeout
        async with ClientSession(timeout=ClientTimeout(total=1)) as session:
            while time.monotonic() < deadline:
                if self.proc is not None and self.proc.returncode is not None:
                    logger.error("Chromium exited early (code=%s) during startup", self.proc.returncode)
                    return False
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            return True
                except (OSError, ConnectionError):
                    pass
                await asyncio.sleep(0.5)
        return False

    async def _kill_process(self) -> None:
        if self.proc is None or self.proc.returncode is not None:
            return
        with suppress(Exception):
            self.proc.terminate()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=GRACEFUL_STOP_TIMEOUT)
        except asyncio.TimeoutError:
            with suppress(Exception):
                self.proc.kill()
            try:
                # This is called with self._restart_lock held (from _restart_browser), so an
                # unreaped process here (e.g. stuck in uninterruptible D-state) would otherwise
                # hang forever and silently wedge every future restart trigger (health check,
                # watchdog, hardware-GL retry) for the rest of the session. Give up waiting after
                # a bounded time instead - the process is already SIGKILLed; if the kernel still
                # can't reap it, no amount of waiting here will fix that.
                await asyncio.wait_for(self.proc.wait(), timeout=GRACEFUL_STOP_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(
                    "Chromium process (pid=%s) did not get reaped %ds after SIGKILL - giving up waiting",
                    self.proc.pid, GRACEFUL_STOP_TIMEOUT,
                )

    async def _watch_process_exit(self, watched_proc: asyncio.subprocess.Process) -> None:
        """Detect Chromium exiting on its own (e.g. a GPU/renderer crash bringing down the whole
        browser) - unlike CDP-level load failures, nothing else notices this, since the CDP
        websocket just silently drops. This is the fast path when it works; '_health_check_loop'
        is the reliable backstop when it doesn't (see the comment in 'start()')."""
        try:
            returncode = await watched_proc.wait()
        except asyncio.CancelledError:
            raise
        if self._stopping or watched_proc is not self.proc:
            return  # Expected shutdown, or superseded by a restart that already replaced self.proc
        self._handle_unexpected_down(f"Chromium process exited unexpectedly (code={returncode})")

    async def _health_check_loop(self) -> None:
        """Poll CDP reachability directly as a restart trigger, independent of process-exit
        detection. Catches both a dead process AND a still-running-but-unresponsive one."""
        url = f"http://{DEFAULT_CDP_HOST}:{DEFAULT_CDP_PORT}/json/version"
        consecutive_failures = 0
        try:
            async with ClientSession(timeout=ClientTimeout(total=HEALTH_CHECK_HTTP_TIMEOUT)) as session:
                while True:
                    await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                    if self._stopping or self._restart_lock.locked():
                        consecutive_failures = 0  # A restart is already in flight; don't pile on
                        continue
                    try:
                        async with session.get(url) as resp:
                            healthy = resp.status == 200
                    except (OSError, ConnectionError, asyncio.TimeoutError):
                        healthy = False

                    # HTTP reachability alone isn't enough: if the CDP websocket reader task has
                    # died (e.g. a connect race, or the websocket dropped after a send() that
                    # never got its response) while Chromium's own HTTP endpoint keeps responding
                    # fine, this would otherwise report "healthy" forever with no working CDP
                    # session and no way to navigate/reload/recover.
                    if healthy and self.conn is not None and not self.conn.connected:
                        healthy = False

                    if healthy:
                        consecutive_failures = 0
                        continue
                    consecutive_failures += 1
                    logger.warning("Chromium health check failed (%d/%d)", consecutive_failures, HEALTH_CHECK_FAILURE_THRESHOLD)
                    if consecutive_failures >= HEALTH_CHECK_FAILURE_THRESHOLD:
                        consecutive_failures = 0
                        self._handle_unexpected_down("Chromium unresponsive (CDP health check failed)")
        except asyncio.CancelledError:
            pass

    def _handle_unexpected_down(self, reason: str) -> None:
        """Shared trigger for both detection paths: escalate GL mode if needed, then restart."""
        if self._stopping:
            return
        logger.error("%s (gl=%s)", reason, self._active_gl_mode)
        if self._active_gl_mode == "hardware":
            logger.warning("Escalating to software (SwiftShader) GL rendering after a hardware-GL crash")
            self._force_software_gl = True
            self._software_gl_since = time.monotonic()
        self._spawn(self._restart_browser(reason))

    async def _hardware_retry_loop(self) -> None:
        """After a hardware-GL crash forces software rendering, periodically give hardware GL
        another chance - a transient crash shouldn't condemn the whole session to SwiftShader's
        far worse performance (very noticeable on anything canvas/WebGL-animation-heavy)."""
        try:
            while True:
                await asyncio.sleep(HARDWARE_GL_RETRY_CHECK_INTERVAL)
                if self._stopping or not self._force_software_gl or self._software_gl_since is None:
                    continue
                if time.monotonic() - self._software_gl_since < HARDWARE_GL_RETRY_INTERVAL:
                    continue
                logger.info(
                    "Retrying hardware GL after %ds of stable software-GL operation",
                    int(time.monotonic() - self._software_gl_since),
                )
                # Deliberately NOT clearing _force_software_gl/_software_gl_since here: if
                # _restart_browser turns out to be a no-op (e.g. _restart_lock is already held by
                # a concurrent crash-triggered restart), clearing them now would permanently
                # disable this loop's own guard above (_software_gl_since becomes None) with no
                # further retry ever attempted - silently stuck on software rendering. Instead
                # pass clear_software_gl=True so _restart_browser only clears them once a
                # hardware-GL launch it actually performed has succeeded.
                self._spawn(self._restart_browser("Retrying hardware GL after cooldown", clear_software_gl=True))
        except asyncio.CancelledError:
            pass

    async def _dpms_watch_loop(self) -> None:
        """Freeze the page while the physical screen is DPMS-blanked (via 'screen_timeout' or the
        REST display_off endpoint - both just flip DPMS state, whether triggered automatically by
        the X server's own idle timer or on demand), so Chromium stops all rendering/compositing/
        JS-timer work for a page nobody can see. Unfreezes and force-reloads when the screen comes
        back on.

        This polls 'xset -q' rather than hooking the REST display_on/off handlers directly,
        because 'screen_timeout' blanks the screen via the X server's own DPMS idle timer without
        ever calling into this add-on at all - polling is the only way to notice that case, not
        just REST-triggered ones.

        Why a full CDP 'Page.setWebLifecycleState' freeze, and not something lighter like just
        spoofing document.hidden via injected JS: a real freeze is what actually stops Chromium's
        compositor/GPU work (confirmed to matter - see the CHANGELOG), not just page-visible
        signals that well-behaved JS might voluntarily honor. The tradeoff is that a frozen page's
        websocket/timers stop being serviced too, so Home Assistant's own frontend won't reconnect
        gracefully on its own - we don't try to make that transition graceful, we just force a
        full reload the moment the screen comes back on instead of trusting the frozen connection
        to resume. That's the right tradeoff here specifically: unlike a transient screensaver
        overlay (seconds to a couple minutes), our screen-off state routinely lasts minutes to
        hours, so the CPU/GPU/heat saved dwarfs a one-time ~1-2s reload flash when someone actually
        looks at the screen again.
        """
        was_on = True
        try:
            while True:
                await asyncio.sleep(DPMS_WATCH_INTERVAL)
                if self._stopping or self.conn is None or self._restart_lock.locked():
                    continue
                is_on = await self._query_dpms_on()
                if is_on is None or is_on == was_on:
                    continue  # Unknown (xset failed) or no transition - nothing to do
                was_on = is_on
                await self._handle_dpms_transition(is_on)
        except asyncio.CancelledError:
            pass

    async def _handle_dpms_transition(self, is_on: bool) -> None:
        """React to a screen on/off transition. Split out from _dpms_watch_loop's polling loop so
        the reaction itself is directly callable/testable without a real 'xset -q' subprocess."""
        assert self.conn is not None
        try:
            if not is_on:
                await self.conn.send("Page.setWebLifecycleState", {"state": "frozen"})
                self._page_frozen = True
                logger.info("Screen off (DPMS): froze the page to stop rendering")
            else:
                await self.conn.send("Page.setWebLifecycleState", {"state": "active"})
                self._page_frozen = False
                logger.info("Screen on (DPMS): unfroze the page and forcing a reload")
                await self.reload(ignore_cache=False)
        except Exception as e:  # pylint: disable=broad-except
            # Never let this optimization become a new source of instability - a failure here
            # just means we stay unfrozen (or try again next transition), not a crash.
            logger.warning("[_dpms_watch_loop] Failed to change page lifecycle state: %s", e)
            self._page_frozen = False

    async def _query_dpms_on(self) -> bool | None:
        """Return whether the monitor is currently on per 'xset -q', or None if that couldn't be
        determined (xset failed, timed out, or produced unexpected output)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "xset", "-q",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=DPMS_QUERY_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            logger.debug("[_dpms_watch_loop] xset -q failed: %s", e)
            return None
        return parse_dpms_monitor_on(stdout.decode(errors="replace"))

    async def _restart_browser(self, reason: str, clear_software_gl: bool = False) -> None:
        if self._restart_lock.locked() or self._stopping:
            return
        async with self._restart_lock:
            now = time.monotonic()
            self._restart_timestamps = [t for t in self._restart_timestamps if now - t < RESTART_WINDOW_SECONDS]
            if len(self._restart_timestamps) >= MAX_RESTARTS_PER_WINDOW:
                logger.error(
                    "GIVING UP: Chromium restarted %d times in the last %ds (%s) - not retrying again. "
                    "Exiting so the add-on can restart fresh.",
                    len(self._restart_timestamps), RESTART_WINDOW_SECONDS, reason,
                )
                self.gave_up.set()
                return
            self._restart_timestamps.append(now)

            logger.error("RESTARTING Chromium (%s): %s", reason, self._current_url)
            if self._watchdog_task:
                self._watchdog_task.cancel()  # No-op if it already finished (e.g. it triggered this restart)
            if self.conn:
                with suppress(Exception):
                    await self.conn.close()
                self.conn = None
            await self._kill_process()
            self._consecutive_failures = 0
            self._settings_applied = False
            try:
                # clear_software_gl=True (hardware-GL retry path) means "actually try hardware for
                # this one attempt", regardless of the persistent _force_software_gl flag - which
                # we deliberately don't touch until we know whether this attempt truly succeeded.
                await self._launch_process(force_software=False if clear_software_gl else None)
                await self._connect_cdp()
            except Exception:
                # If this fails partway (e.g. _connect_cdp raises after Chromium's HTTP endpoint
                # is already up), don't leave self.conn set to a half-configured connection - the
                # health check's HTTP-reachability poll would then keep reporting "healthy"
                # forever with no working CDP session and no event handlers registered. Clean up
                # fully and let the health check's own polling notice we're down and retry
                # (still bounded by the restart-rate-limiter above).
                logger.exception("RESTART FAILED (%s): could not relaunch/reconnect Chromium", reason)
                if self.conn:
                    with suppress(Exception):
                        await self.conn.close()
                    self.conn = None
                await self._kill_process()
                raise
            if clear_software_gl:
                if self._active_gl_mode == "hardware":
                    # The retry actually landed on hardware - stable again, stop tracking a cooldown.
                    self._force_software_gl = False
                    self._software_gl_since = None
                else:
                    # _launch_process's own hardware->software fallback kicked in again within this
                    # same attempt (hardware still doesn't work). Re-arm exactly as a fresh crash
                    # would: keep forcing software and restart the cooldown, so future launches skip
                    # straight to software again and the retry loop tries once more after another
                    # full interval, instead of silently giving up (if we left the flags cleared) or
                    # never retrying again (if we'd cleared them before knowing the outcome).
                    self._force_software_gl = True
                    self._software_gl_since = time.monotonic()
            self._watchdog_task = asyncio.create_task(self._watch_process_exit(self.proc))
            self._reset_refresh_timer()

    # ------------------------------------------------------------------ #
    # CDP session setup
    # ------------------------------------------------------------------ #
    async def _connect_cdp(self) -> None:
        self.conn = await CDPConnection.connect(DEFAULT_CDP_HOST, DEFAULT_CDP_PORT)
        await self.conn.send("Page.enable")
        await self.conn.send("Network.enable")
        await self.conn.send("Runtime.enable")

        # Emulate prefers-color-scheme without Chromium's page-recoloring "Force Dark" heuristic
        await self.conn.send("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": "dark" if self.dark_mode else "light"}]
        })

        if self.voice_satellite:
            # Pre-grant mic access before the first page even finishes loading so Voice
            # Satellite's getUserMedia succeeds without a prompt or user gesture.
            await self._grant_microphone_permission()

        if self.browser_language:
            # --lang (in _build_args) picks which locale Chromium's own UI/spellchecker uses, but
            # doesn't reliably reach page-visible signals in every Chromium build. Belt-and-suspenders
            # it here at the CDP level, which is what actually determines the language Home
            # Assistant's frontend auto-detects (it reads navigator.language/Accept-Language,
            # whenever the user hasn't picked a language in their own HA profile):
            #   - Emulation.setLocaleOverride: Intl.*.resolvedOptions().locale / date-time formatting
            #   - Network.setExtraHTTPHeaders: the Accept-Language header sent with every request
            #   - The injected script below (registered with the other on-new-document scripts):
            #     navigator.language / navigator.languages, which - verified on a real device -
            #     Emulation.setLocaleOverride does *not* actually touch on this Chromium build (only
            #     Intl.* changed; navigator.language stayed whatever Chromium's untouched default
            #     was, even across a full ignoreCache reload)
            # All are process-wide overrides (unlike Page.navigate), so - like the dark-mode
            # emulation above - they're set once here and apply to every subsequent navigation/
            # reload, not just the current page.
            with suppress(Exception):
                await self.conn.send("Emulation.setLocaleOverride", {"locale": self.browser_language})
            with suppress(Exception):
                await self.conn.send("Network.setExtraHTTPHeaders",
                                      {"headers": {"Accept-Language": self._accept_language_header()}})

        # Scripts that must run before every page's own scripts (persist across reloads/navigations)
        scripts = [self._suppress_errors_js(), self._ws_recovery_js()]
        if self.browser_language:
            scripts.append(self._locale_spoof_js())
        for script in scripts:
            await self.conn.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})

        self.conn.on("Page.frameNavigated", self._on_frame_navigated)
        self.conn.on("Page.loadEventFired", self._on_load_event)
        self.conn.on("Network.loadingFailed", self._on_loading_failed)

        # Log Chromium's own authoritative GPU status right away - the same data chrome://gpu
        # reads from - so it shows up automatically in the add-on log without needing a separate
        # GET /kiosk_status call. Our own hardware/software GL-mode tracking only reflects which
        # launch flags we used and whether the process stayed up, not whether GPU compositing/
        # rasterization/WebGL are actually active end to end, which is what actually determines
        # animation performance for canvas/WebGL-heavy dashboard content.
        gpu_info = await self.get_gpu_info()
        if gpu_info:
            logger.info("Chromium GPU status: renderer=%s vendor=%s feature_status=%s",
                        gpu_info.get("gl_renderer"), gpu_info.get("gl_vendor"), gpu_info.get("feature_status"))
        else:
            logger.warning("Could not retrieve Chromium GPU status (SystemInfo.getInfo failed)")

    def _locale_spoof_js(self) -> str:
        """Override navigator.language/navigator.languages via a defineProperty on Navigator.prototype,
        injected before any page script runs (Page.addScriptToEvaluateOnNewDocument). Needed because
        Emulation.setLocaleOverride - confirmed on a real device - does not actually change these two
        properties on this Chromium build (only Intl.*.resolvedOptions() changed), even immediately
        after a full ignoreCache reload. Most sites, including Home Assistant's own initial-load auto-
        detection, read navigator.language directly rather than going through Intl."""
        lang = self.browser_language
        base = lang.split("-")[0]
        langs = [lang, base] if base != lang else [lang]
        return f"""
            (function() {{
                try {{
                    Object.defineProperty(Navigator.prototype, 'language', {{
                        get: function() {{ return {json.dumps(lang)}; }}, configurable: true
                    }});
                    Object.defineProperty(Navigator.prototype, 'languages', {{
                        get: function() {{ return Object.freeze({json.dumps(langs)}); }}, configurable: true
                    }});
                }} catch (e) {{ console.warn('Failed to override navigator.language:', e); }}
            }})();
        """

    def _accept_language_header(self) -> str:
        """Build a quality-weighted Accept-Language value from the configured locale, e.g.
        'es-ES' -> 'es-ES,es;q=0.9', so a region-specific choice still credibly offers its base
        language as a fallback instead of just the one exact tag."""
        lang = self.browser_language
        base = lang.split("-")[0]
        if base and base != lang:
            return f"{lang},{base};q=0.9"
        return lang

    # ------------------------------------------------------------------ #
    # CDP event handlers (sync callbacks that schedule async work)
    # ------------------------------------------------------------------ #
    def _on_frame_navigated(self, params: dict[str, Any]) -> None:
        frame = params.get("frame", {})
        if "parentId" in frame:  # Sub-frame (iframe); only track the main frame
            return
        # Note: deliberately NOT resetting '_settings_applied' here - this fires on every
        # navigation, including periodic-refresh reloads, and re-forcing the sidebar/theme
        # localStorage settings on every reload would silently clobber changes the user
        # made by hand in the HA frontend since the kiosk started. Settings are (re)applied
        # once per browser session (i.e. per explicit 'navigate()' call or Chromium restart).
        self._current_url = frame.get("url", "")

        auth_prefix = self.ha_url_base + "/auth/authorize?response_type=code"
        if self._current_url.startswith(auth_prefix):
            self._spawn(self._do_auto_login())

    def _on_load_event(self, _params: dict[str, Any]) -> None:
        self._consecutive_failures = 0
        self._spawn(self._on_page_loaded())

    def _on_loading_failed(self, params: dict[str, Any]) -> None:
        if params.get("type") != "Document" or params.get("canceled"):
            return
        self._consecutive_failures += 1
        logger.warning("Page load failed (%d/%d): %s [%s]",
                        self._consecutive_failures, MAX_LOAD_FAILURES, self._current_url, params.get("errorText"))
        if self._consecutive_failures >= MAX_LOAD_FAILURES:
            self._spawn(self._restart_browser("too many consecutive load failures"))

    async def _on_page_loaded(self) -> None:
        url = self._current_url

        if self.onscreen_keyboard:
            with suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "dbus-send", "--type=method_call", "--dest=org.onboard.Onboard",
                    "/org/onboard/Onboard/Keyboard", "org.onboard.Onboard.Keyboard.Hide",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                # This runs on every navigation (initial load, periodic refresh, hard reload,
                # websocket-recovery reload). 'suppress(Exception)' only catches exceptions, not a
                # hang - if the D-Bus session is ever slow/unresponsive, unbounded proc.wait() calls
                # here would accumulate indefinitely. Bound it and kill on timeout.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=DBUS_SEND_TIMEOUT)
                except asyncio.TimeoutError:
                    with suppress(Exception):
                        proc.kill()
                    with suppress(Exception):
                        await proc.wait()

        is_auth_page = url.startswith(self.ha_url_base + "/auth/")
        under_ha = (url + "/").startswith(self.ha_url_base + "/")
        if not self._settings_applied and under_ha and not is_auth_page:
            # Only mark as applied if the JS actually ran - otherwise a transient failure (most
            # commonly: this load event fired for HA's unauthenticated "/" shell right as it was
            # about to client-side-redirect to /auth/authorize, so by the time our eval reaches
            # Chromium the target has already navigated away - would silently and permanently
            # skip applying sidebar/theme settings for the rest of the session.
            self._settings_applied = await self._apply_ha_settings()

        if self.voice_satellite and under_ha and not is_auth_page:
            # Runs on every dashboard load (initial boot, periodic refresh, websocket-recovery
            # reload, post-DPMS reload) since Voice Satellite tears itself down on 'pagehide'
            # and must come back up on each new page.
            self._spawn(self._voice_satellite_autostart())

    async def _do_auto_login(self) -> None:
        if not self.ha_username or not self.ha_password:
            return
        js = f"""
            setTimeout(function() {{
                try {{
                    const haInputs = document.querySelectorAll('ha-input');
                    const usernameField = haInputs[0]?.shadowRoot?.querySelector('wa-input')?.shadowRoot?.querySelector('input[autocomplete="username"]')
                        || document.querySelector('input[autocomplete="username"]');
                    const passwordField = haInputs[1]?.shadowRoot?.querySelector('wa-input')?.shadowRoot?.querySelector('input[autocomplete="current-password"]')
                        || document.querySelector('input[autocomplete="current-password"]');
                    const haCheckbox = document.querySelector('ha-checkbox');
                    const submitButton = document.querySelector('ha-button');

                    if (usernameField && passwordField) {{
                        usernameField.value = {json.dumps(self.ha_username)};
                        usernameField.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        usernameField.dispatchEvent(new Event('change', {{ bubbles: true }}));

                        passwordField.value = {json.dumps(self.ha_password)};
                        passwordField.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        passwordField.dispatchEvent(new Event('change', {{ bubbles: true }}));

                        console.log('Auto-login: fields filled + events dispatched');
                    }} else {{
                        console.log('Auto-login failed: missing elements', {{
                            username: !!usernameField, password: !!passwordField, submit: !!submitButton
                        }});
                    }}

                    if (haCheckbox) {{
                        haCheckbox.setAttribute('checked', '');
                        haCheckbox.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                    if (submitButton) submitButton.click();
                }} catch(e) {{ console.warn('Auto-login JS error:', e); }}
            }}, {int(self.login_delay * 1000)});
        """
        await self._eval_js(js, "auto_login")

    async def _apply_ha_settings(self) -> bool:
        js = f"""
            try {{
                localStorage.setItem('browser_mod-browser-id', 'haos_kiosk');

                const sidebar = '{_single_quote_escape(self.sidebar_js_value)}';
                const currentSidebar = localStorage.getItem('dockedSidebar') || '';
                if (sidebar !== currentSidebar) {{
                    if (sidebar !== '') localStorage.setItem('dockedSidebar', sidebar);
                    else localStorage.removeItem('dockedSidebar');
                }}

                const theme = '{_single_quote_escape(self.theme_js_value)}';
                const currentTheme = localStorage.getItem('selectedTheme') || '';
                if (theme !== currentTheme) {{
                    if (theme !== '') localStorage.setItem('selectedTheme', theme);
                    else localStorage.removeItem('selectedTheme');
                }}
            }} catch (err) {{
                console.error('Failed to set HA sidebar/theme settings:', err);
            }}
        """
        return await self._eval_js(js, "ha_settings")

    # ------------------------------------------------------------------ #
    # Voice Satellite auto-start
    # ------------------------------------------------------------------ #
    def _seed_profile_preferences(self) -> None:
        """Seed the freshly-wiped profile with Chromium Preferences that silence UI prompts a
        headless kiosk can never answer:

          - Password manager fully off ('Save password?' bubble after the HA auto-login fills
            the login form - pre-existing annoyance, now gone).
          - With voice_satellite: microphone content-setting exception set to ALLOW for exactly
            the HA origin. This is the race-free complement to _grant_microphone_permission:
            that one runs over CDP after launch and can lose the race against Voice Satellite's
            own getUserMedia on a fast first page load, while this file is read by Chromium
            before any page exists, so there is never a prompt in the first place.

        Written before every launch (the profile is wiped each time), into the 'Default'
        profile dir Chromium will create anyway."""
        prefs: dict[str, Any] = {
            "credentials_enable_service": False,
            "credentials_enable_autosignin": False,
            "profile": {
                "password_manager_enabled": False,
            },
        }
        if self.voice_satellite:
            prefs["profile"]["content_settings"] = {
                "exceptions": {
                    # '<origin>,*' = primary-pattern + embedded wildcard, setting 1 = ALLOW
                    # (Chromium's CONTENT_SETTING_ALLOW). Same value CDP's grantPermissions sets.
                    "media_mic": {
                        f"{self.ha_url_base},*": {"setting": 1},
                    },
                }
            }
        prefs_path = os.path.join(PROFILE_DIR, "Default")
        try:
            os.makedirs(prefs_path, exist_ok=True)
            with open(os.path.join(prefs_path, "Preferences"), "w", encoding="utf-8") as f:
                json.dump(prefs, f)
            logger.debug("Seeded Chromium profile preferences (%s)", ", ".join(sorted(
                ("mic-allow" if self.voice_satellite else "", "no-password-manager"))))
        except OSError as e:
            logger.warning("Could not seed Chromium profile preferences (%s) - password/mic "
                           "prompts may appear", e)

    async def _grant_microphone_permission(self) -> None:
        """Pre-grant microphone access for the HA origin via CDP's Browser.grantPermissions so
        Voice Satellite's getUserMedia call succeeds immediately - no permission prompt and,
        crucially, no user-gesture requirement (the profile dir is wiped on every launch, so a
        persisted 'allow' from a previous session never survives). The Browser.* domain lives on
        the browser-level CDP target only, hence the short-lived second connection - the same
        pattern get_gpu_info uses."""
        browser_conn: CDPConnection | None = None
        try:
            browser_conn = await CDPConnection.connect_browser(DEFAULT_CDP_HOST, DEFAULT_CDP_PORT)
            await browser_conn.send("Browser.grantPermissions", {
                "permissions": ["audioCapture"],
                "origin": self.ha_url_base,
            })
            logger.info("Voice Satellite: granted microphone access for %s", self.ha_url_base)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                "Voice Satellite: could not pre-grant microphone access (%s) - if the wake-word "
                "engine then parks behind its floating start button, it will be tapped anyway; "
                "if that also fails, tap the button manually once", e,
            )
        finally:
            if browser_conn is not None:
                with suppress(Exception):
                    await browser_conn.close()

    # Locates Voice Satellite's floating 'tap to start' overlay button (#voice-satellite-ui is
    # the global engine UI injected on every page; see voice-satellite-card-integration's
    # card/ui.js). Returns the viewport-space center of the button for Input.dispatchMouseEvent,
    # or null when the button isn't showing (engine already running or not configured).
    _VS_START_BUTTON_JS = """
        (function() {
            const btn = document.querySelector('#voice-satellite-ui .vs-start-btn');
            if (!btn || !btn.classList.contains('visible')) return null;
            const r = btn.getBoundingClientRect();
            if (!(r.width > 0 && r.height > 0)) return null;
            return JSON.stringify({x: r.x + r.width / 2, y: r.y + r.height / 2});
        })()
    """

    async def _eval_js_value(self, js: str) -> Any:
        """Runtime.evaluate with returnByValue, returning the expression's JSON value or None.
        Like _eval_js but for expressions whose result matters."""
        if self.conn is None:
            return None
        try:
            result = await self.conn.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
            if result.get("exceptionDetails"):
                return None
            return result.get("result", {}).get("value")
        except Exception as e:  # pylint: disable=broad-except
            logger.debug("[_eval_js_value] Failed to evaluate JS: %s", e)
            return None

    async def _voice_satellite_autostart(self) -> None:
        """Watch for Voice Satellite's floating 'tap to start' button after each page load and
        tap it via CDP input events.

        With mic access pre-granted (_grant_microphone_permission) Voice Satellite's own
        auto-start normally succeeds silently. This is the belt-and-suspenders path for when
        the browser still parks the engine behind the floating button (e.g. an AudioContext
        resume refused without user activation): a CDP-dispatched mouse press/release is a
        *trusted* renderer input event, so it carries real user activation and unblocks
        everything a human finger tap would - which plain element.click() JS would not.
        """
        deadline = time.monotonic() + VS_AUTOSTART_MAX_WAIT
        taps = 0
        while time.monotonic() < deadline and taps < VS_AUTOSTART_MAX_TAPS:
            await asyncio.sleep(VS_AUTOSTART_POLL_INTERVAL)
            if (self._stopping or self._page_frozen or self.conn is None
                    or not self.conn.connected or self._restart_lock.locked()):
                return
            raw = await self._eval_js_value(self._VS_START_BUTTON_JS)
            if not raw:
                continue  # Button not showing - engine either running fine or not yet ready
            try:
                pos = json.loads(raw)
            except (TypeError, ValueError):
                continue
            taps += 1
            logger.info("Voice Satellite: tapping floating start button (attempt %d/%d)",
                        taps, VS_AUTOSTART_MAX_TAPS)
            click = {"x": pos.get("x", 0), "y": pos.get("y", 0), "button": "left", "clickCount": 1}
            try:
                await self.conn.send("Input.dispatchMouseEvent", {"type": "mousePressed", **click})
                await self.conn.send("Input.dispatchMouseEvent", {"type": "mouseReleased", **click})
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Voice Satellite: failed to tap start button: %s", e)
                continue
            # Give the engine a moment to spin up, then confirm the tap actually dismissed
            # the button; otherwise keep polling until another attempt fits in the window.
            await asyncio.sleep(VS_AUTOSTART_RECHECK_DELAY)
            if not await self._eval_js_value(self._VS_START_BUTTON_JS):
                logger.info("Voice Satellite: engine started hands-free")
                return

    async def _eval_js(self, js: str, label: str) -> bool:
        if self.conn is None:
            return False
        try:
            result = await self.conn.send("Runtime.evaluate", {"expression": js, "awaitPromise": False})
            if result.get("exceptionDetails"):
                logger.warning("[%s] JS exception: %s", label, result["exceptionDetails"])
                return False
            return True
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[%s] Failed to evaluate JS: %s", label, e)
            return False

    @staticmethod
    def _suppress_errors_js() -> str:
        # Suppress known harmless unhandled promise rejections in the kiosk environment
        # (service worker/script load failures during reloads, view-transition errors when
        # the screen is off) without hiding real errors.
        return """
            window.addEventListener('unhandledrejection', function(e) {
                const reason = e.reason;
                let suppress = false;
                if (reason) {
                    const msg = typeof reason.message === 'string' ? reason.message : '';
                    const name = (reason.name || '').toLowerCase();
                    if (msg.includes('sw-modern.js') ||
                        msg.includes('load failed') ||
                        msg.includes('service worker') ||
                        (name === 'invalidstateerror' &&
                            (msg.includes('document visibility state is hidden') ||
                             msg.includes('view transition'))) ||
                        reason === '[object Object]' ||
                        msg === '' ||
                        typeof reason === 'object') {
                        suppress = true;
                    }
                }
                if (suppress) {
                    console.warn('Suppressed known kiosk-safe unhandled rejection:', reason);
                    e.preventDefault();
                }
            });
        """

    @staticmethod
    def _ws_recovery_js() -> str:
        # Force a reload if the HA websocket connection stays dead for >10s (common after reconnect failures)
        return """
            (function() {
                if (window.ha_ws_recovery_interval) return;
                window.ha_ws_recovery_interval = setInterval(function() {
                    if (window.APP && window.APP.connection && !window.APP.connection.connected) {
                        console.warn('HA websocket dead >10s - forcing reload for recovery');
                        location.reload();
                    }
                }, 10000);
            })();
        """

    # ------------------------------------------------------------------ #
    # Periodic refresh
    # ------------------------------------------------------------------ #
    def _reset_refresh_timer(self) -> None:
        self._refresh_deadline = time.monotonic() + self.browser_refresh

    async def _refresh_loop(self) -> None:
        try:
            while True:
                remaining = self._refresh_deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    continue
                if self._current_url and self._current_url != "about:blank" and self.conn is not None:
                    self._hard_reload_count += 1
                    bypass_cache = self._hard_reload_count % HARD_RELOAD_FREQ == 0
                    logger.info("RELOADING%s: %s", " [HARD]" if bypass_cache else "", self._current_url)
                    try:
                        await self.conn.send("Page.reload", {"ignoreCache": bypass_cache})
                    except Exception as e:  # pylint: disable=broad-except
                        logger.warning("Periodic reload failed: %s", e)
                self._reset_refresh_timer()
        except asyncio.CancelledError:
            pass
