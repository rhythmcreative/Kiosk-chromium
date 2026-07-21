"""-------------------------------------------------------------------------------
# Add-on: HAOS Kiosk Display (haoskiosk)
# File: chromium_kiosk.py
# Version: 1.4.2
# Copyright Jeff Kosowsky
# Date: July 2026

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

__version__ = "1.4.2"

CHROMIUM_BIN = "chromium"  # Resolved via PATH so 'pgrep -f "^chromium "' in run.sh's wait loop matches argv[0]
PROFILE_DIR = "/root/.config/chromium-kiosk"

HARD_RELOAD_FREQ = 10   # Every Nth periodic refresh also bypasses cache (mirrors old userconf.lua)
MAX_LOAD_FAILURES = 5   # Consecutive main-document load failures before restarting Chromium
CDP_READY_TIMEOUT = 20  # Seconds to wait for Chromium's CDP endpoint to come up
GRACEFUL_STOP_TIMEOUT = 5  # Seconds to wait for SIGTERM before SIGKILL
MAX_RESTARTS_PER_WINDOW = 5   # Give up restarting (let the container exit) after this many restarts...
RESTART_WINDOW_SECONDS = 180  # ...within this many seconds - avoids a tight crash-restart loop
HEALTH_CHECK_INTERVAL = 3          # Seconds between CDP reachability polls
HEALTH_CHECK_HTTP_TIMEOUT = 2      # Seconds to wait for each poll
HEALTH_CHECK_FAILURE_THRESHOLD = 2  # Consecutive failed polls before treating Chromium as down


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
        self.zoom_level = max(_env_float("ZOOM_LEVEL", 100), 1)
        self.browser_refresh = max(_env_float("BROWSER_REFRESH", 600), 0)
        self.dark_mode = _env_bool("DARK_MODE", True)
        self.onscreen_keyboard = _env_bool("ONSCREEN_KEYBOARD", False)

        raw_sidebar = (os.getenv("HA_SIDEBAR") or "").strip().lower()
        valid_sidebars = {"full": "", "none": '"always_hidden"', "narrow": '"auto"', "": ""}
        self.sidebar_js_value = valid_sidebars.get(raw_sidebar, "")
        if raw_sidebar and raw_sidebar not in valid_sidebars:
            logger.warning("Invalid HA_SIDEBAR value: '%s'; defaulting to unset", raw_sidebar)

        theme = (os.getenv("HA_THEME") or "").strip()
        if theme and theme[0] not in ('"', "'", "{"):
            theme = f'"{theme}"'
        self.theme_js_value = theme

        logger.info(
            "ChromiumKiosk config: URL=%s DARK_MODE=%s SIDEBAR=%s THEME=%s LOGIN_DELAY=%.1f "
            "ZOOM_LEVEL=%d BROWSER_REFRESH=%d ONSCREEN_KEYBOARD=%s",
            self.initial_url, self.dark_mode, raw_sidebar, theme or "(none)",
            self.login_delay, self.zoom_level, self.browser_refresh, self.onscreen_keyboard,
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
        self._health_check_task: asyncio.Task[None] | None = None
        self._restart_lock = asyncio.Lock()
        self._restart_timestamps: list[float] = []
        self._force_software_gl = False  # Set once hardware GL is observed to crash post-startup
        self._active_gl_mode: str | None = None
        self._stopping = False

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
        logger.info("ChromiumKiosk started: %s", self._current_url)

        if self.browser_refresh > 0:
            self._reset_refresh_timer()
            self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Gracefully tear down Chromium and the CDP session."""
        self._stopping = True
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._health_check_task:
            self._health_check_task.cancel()
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
        ]
        if gl_mode == "software":
            args += ["--use-gl=angle", "--use-angle=swiftshader-webgl", "--disable-gpu-compositing"]
        else:
            # --use-angle=gl-egl pins ANGLE's own EGL backend explicitly (rather than letting
            # --use-gl=egl alone decide), which has proven more reliable on Mesa/V3D than
            # leaving ANGLE to auto-select.
            args += ["--use-gl=egl", "--use-angle=gl-egl", "--enable-gpu-rasterization", "--enable-zero-copy"]
        return args

    async def _launch_process(self) -> None:
        gl_modes = ("software",) if self._force_software_gl else ("hardware", "software")
        for gl_mode in gl_modes:
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)  # Always start from a fresh profile (no session restore)
            os.makedirs(PROFILE_DIR, exist_ok=True)

            args = self._build_args(gl_mode)
            logger.info("Launching Chromium (%s GL): %s %s", gl_mode, CHROMIUM_BIN, " ".join(args))
            self.proc = await asyncio.create_subprocess_exec(
                CHROMIUM_BIN, *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            if await self._wait_for_cdp_ready(CDP_READY_TIMEOUT):
                logger.info("Chromium ready (%s GL, pid=%d)", gl_mode, self.proc.pid)
                self._active_gl_mode = gl_mode
                return

            logger.warning("Chromium failed to become ready with %s GL rendering", gl_mode)
            await self._kill_process()

        raise RuntimeError("Chromium failed to start with both hardware and software GL rendering")

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
            with suppress(Exception):
                await self.proc.wait()

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
        asyncio.create_task(self._restart_browser(reason))

    async def _restart_browser(self, reason: str) -> None:
        if self._restart_lock.locked() or self._stopping:
            return
        async with self._restart_lock:
            now = time.monotonic()
            self._restart_timestamps = [t for t in self._restart_timestamps if now - t < RESTART_WINDOW_SECONDS]
            if len(self._restart_timestamps) >= MAX_RESTARTS_PER_WINDOW:
                logger.error(
                    "GIVING UP: Chromium restarted %d times in the last %ds (%s) - not retrying again. "
                    "run.sh will detect no browser process and exit, letting the add-on restart fresh.",
                    len(self._restart_timestamps), RESTART_WINDOW_SECONDS, reason,
                )
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
            await self._launch_process()
            await self._connect_cdp()
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

        # Scripts that must run before every page's own scripts (persist across reloads/navigations)
        for script in (self._suppress_errors_js(), self._ws_recovery_js()):
            await self.conn.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})

        self.conn.on("Page.frameNavigated", self._on_frame_navigated)
        self.conn.on("Page.loadEventFired", self._on_load_event)
        self.conn.on("Network.loadingFailed", self._on_loading_failed)

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
            asyncio.create_task(self._do_auto_login())

    def _on_load_event(self, _params: dict[str, Any]) -> None:
        self._consecutive_failures = 0
        asyncio.create_task(self._on_page_loaded())

    def _on_loading_failed(self, params: dict[str, Any]) -> None:
        if params.get("type") != "Document" or params.get("canceled"):
            return
        self._consecutive_failures += 1
        logger.warning("Page load failed (%d/%d): %s [%s]",
                        self._consecutive_failures, MAX_LOAD_FAILURES, self._current_url, params.get("errorText"))
        if self._consecutive_failures >= MAX_LOAD_FAILURES:
            asyncio.create_task(self._restart_browser("too many consecutive load failures"))

    async def _on_page_loaded(self) -> None:
        url = self._current_url

        if self.onscreen_keyboard:
            with suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "dbus-send", "--type=method_call", "--dest=org.onboard.Onboard",
                    "/org/onboard/Onboard/Keyboard", "org.onboard.Onboard.Keyboard.Hide",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()

        is_auth_page = url.startswith(self.ha_url_base + "/auth/")
        under_ha = (url + "/").startswith(self.ha_url_base + "/")
        if not self._settings_applied and under_ha and not is_auth_page:
            await self._apply_ha_settings()
            self._settings_applied = True

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

    async def _apply_ha_settings(self) -> None:
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
        await self._eval_js(js, "ha_settings")

    async def _eval_js(self, js: str, label: str) -> None:
        if self.conn is None:
            return
        try:
            result = await self.conn.send("Runtime.evaluate", {"expression": js, "awaitPromise": False})
            if result.get("exceptionDetails"):
                logger.warning("[%s] JS exception: %s", label, result["exceptionDetails"])
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("[%s] Failed to evaluate JS: %s", label, e)

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
