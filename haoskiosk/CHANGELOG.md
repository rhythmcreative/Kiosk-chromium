# Changelog

## v1.4.11 - July 2026

- **Confirmed via real `gpu_info` output** (thanks to the v1.4.10 fix):
  GPU acceleration is completely disabled on at least one real device -
  `opengl: disabled_off`, `webgl: disabled_off`,
  `gpu_compositing: disabled_software`, `rasterization: disabled_software`,
  empty GL renderer/vendor strings - despite our own tracking correctly
  reporting "hardware GL" (the Chromium *process* stays up fine; only its
  internal GPU process/feature initialization silently fails). None of the
  fixes so far could have caught this, since nothing crashes at the
  process or CDP level - Chromium just quietly runs everything in
  software.
- We were discarding Chromium's own stderr entirely
  (`stderr=DEVNULL`), which is where the actual EGL/GBM/Mesa/GPU-process
  error message would appear - so there was no way to see *why* GPU init
  was failing, only that it had. Now captured via a background reader
  task; lines matching GPU-related keywords (gpu, egl, gbm, gl error,
  vulkan, angle, mesa, dri, v3d, vc4) are logged at WARNING (so they show
  up in the add-on's regular log automatically), everything else at DEBUG
  to avoid flooding it with Chromium's usual unrelated noise

## v1.4.10 - July 2026

- **Fix:** the `gpu_info` GPU-status logging/endpoint added in v1.4.7/v1.4.9
  never actually worked - confirmed from a real deployment log:
  `SystemInfo.getInfo failed: ... "SystemInfo.getInfo is only supported on
  the browser target"`. `SystemInfo.getInfo` is only available on
  Chromium's browser-level CDP target, not the per-tab page target
  `ChromiumKiosk.conn` is connected to (which is correct for everything
  else - Page, Network, Runtime, Emulation). Added
  `cdp_client.get_browser_websocket_url()` (from `/json/version`, distinct
  from the page target list at `/json/list`) and
  `CDPConnection.connect_browser()`; `get_gpu_info()` now opens its own
  short-lived connection to that target instead of reusing `self.conn`.
  Verified against a fake CDP server reproducing the exact two-target
  split (page target rejects the call, browser target accepts it) before
  shipping

## v1.4.9 - July 2026

- Chromium's real GPU feature status (`gpu_compositing`, `rasterization`,
  `webgl`, GPU renderer/vendor - same data `chrome://gpu` reads from) is
  now logged automatically right after startup, instead of only being
  available via a separate `GET /kiosk_status` call. Pasting the add-on's
  regular startup log is now enough to see whether GPU acceleration is
  actually active, without needing a way to run `curl` against the add-on
  (not always straightforward to do from HAOS)

## v1.4.8 - July 2026

- **Fix:** the `[ha_settings] Failed to evaluate JS: ... Inspected target
  navigated or closed` warning seen on every fresh start (harmless on its
  own - it's HA's frontend client-side-redirecting an unauthenticated "/"
  load to `/auth/authorize` right as our settings-injection eval reaches
  Chromium) was silently and *permanently* skipping the HA sidebar/theme
  settings for the rest of the session. `_settings_applied` was set `True`
  unconditionally after attempting the injection, regardless of whether it
  actually succeeded. Now only marks it applied on an actual success, so a
  failed attempt gets retried on the next real dashboard load instead of
  giving up silently

## v1.4.7 - July 2026

- Added `gpu_info` to the `GET /kiosk_status` response: Chromium's own
  authoritative GPU feature status via CDP's `SystemInfo.getInfo` - the
  exact same data `chrome://gpu` itself reads from (`gpu_compositing`,
  `rasterization`, `webgl` feature status, GPU device/driver strings). The
  existing `gl_mode`/`forced_software_gl` fields only reflect which launch
  flags we used and whether the process stayed up; they can't tell you
  whether GPU compositing/rasterization/WebGL are *actually* active end to
  end, which is what actually determines animation performance

## v1.4.6 - July 2026

- **Fix: real root cause of GPU-heavy content (canvas/WebGL animations)
  rendering at ~2fps despite "hardware GL" mode reporting correctly.** The
  v1.4.4 flag `--use-angle=gl-egl` was added believing it "pins ANGLE's EGL
  backend more reliably" - that reasoning was wrong. `gl-egl` is a real
  ANGLE backend value, but it means "translate ES-style draw calls into
  **desktop OpenGL** via EGL". Raspberry Pi's V3D driver only natively
  implements OpenGL ES (no desktop GL), so this flag forced every draw call
  through an unnecessary ES-to-desktop-GL translation shim. It didn't crash
  or trigger the software-GL fallback (so every diagnostic we'd built -
  logs, `/kiosk_status` - correctly reported "hardware" mode, hiding the
  actual problem), it just made GPU-heavy content crawl. Verified against
  Chromium's actual `ui/gl/gl_switches.cc` source and real-world working
  Raspberry Pi Chromium kiosk configs (none of which pin `--use-angle`
  explicitly) before making this change, rather than guessing again.
  Removed the flag entirely; Chromium's own backend auto-selection picks
  the correct ANGLE backend for the driver instead

## v1.4.5 - July 2026

- **Fix: permanently stuck on software (SwiftShader) GL rendering.** Once a
  single hardware-GL crash forced software rendering, the add-on never
  tried hardware again for the rest of that container's life - even if the
  crash was a one-off transient issue. Software rendering is *far* slower
  for anything canvas/WebGL-animation-heavy (custom dashboard cards with
  visual effects in particular can drop to a couple of frames per second),
  so a session that got unlucky once during startup would silently stay
  slow indefinitely with no further errors logged. Added a background task
  that retries hardware GL after 30 minutes of stable software-GL
  operation; if hardware crashes again it falls back to software and the
  cooldown starts over, so a persistently broken GPU still degrades
  gracefully rather than crash-looping
- Added `GET /kiosk_status` REST endpoint reporting whether Chromium is
  currently on hardware or software GL, how long it's been on software (if
  so), and other kiosk-controller state - so this kind of issue is
  instantly diagnosable instead of requiring a full log dump

## v1.4.4 - July 2026

- **Performance:** Chromium's power-saving heuristics can throttle JS
  timers/`requestAnimationFrame` and deprioritize rendering for a window it
  thinks is unfocused/occluded - easy to trip under a bare window manager
  with no decorations, and the single biggest cause of a kiosk dashboard
  feeling laggy/stale rather than an actual rendering bottleneck. Disabled
  unconditionally via `--disable-background-timer-throttling`,
  `--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding`,
  `--disable-ipc-flooding-protection`, `--disable-hang-monitor`. Also disabled
  Site Isolation (`--disable-site-isolation-trials`, `--renderer-process-limit=1`)
  since this is always a single trusted origin in a single `--app` window -
  the extra process/IPC overhead it adds buys nothing here
- **Fix: onscreen keyboard never appeared.** Onboard was starting fine (dconf
  settings applied, `auto-show`/`force-to-top` set) but its window was
  getting stacked *below* Chromium's true-fullscreen `--kiosk` window -
  Onboard's own "always on top" request isn't enough to win against that,
  only the window manager's own layering rules are. Added an Openbox
  `<applications>` rule forcing Onboard's window onto the "above" layer,
  which Openbox does respect even over a fullscreen window. (Investigated
  wiring up full AT-SPI-based auto-show, i.e. Onboard automatically
  detecting text-field focus inside Chromium's page content - not currently
  feasible: Alpine only packages the AT-SPI registry daemon, not the
  GTK/ATK bridge library apps need to actually expose accessibility info to
  it, and that bridge isn't available to install from Alpine's repos)

## v1.4.3 - July 2026

- **Fix:** despite the v1.4.2 CDP health-check backstop, a real deployment
  still hit the add-on exiting a few seconds after Chromium started - with
  *neither* crash-detection path (process-exit watchdog or health check)
  ever logging anything. That, combined with the consistent ~8-9s timing
  across multiple attempts regardless of Chromium-side changes, points at
  `run.sh`'s own `pgrep -f "^chromium "` polling loop being the unreliable
  part, not Chromium itself.
- Removed that pgrep-based polling entirely. `run.sh` now simply waits on
  the PID of the REST server process (`wait "$REST_SERVER_PID"`), which is
  the component that actually drives Chromium and already knows
  authoritatively whether it's healthy. `chromium_kiosk.py` exposes a new
  `gave_up` event, set only when the restart-rate-limiter permanently gives
  up; `rest_server.py`'s `main()` now exits on whichever comes first of
  SIGTERM or that event, so `run.sh` finds out immediately and directly
  instead of inferring it indirectly through process-name polling.

## v1.4.2 - July 2026

- **Fix:** the v1.4.1 crash-recovery watchdog relied solely on
  `asyncio.subprocess.Process.wait()` to detect Chromium exiting - which
  depends on asyncio's child-watcher/SIGCHLD machinery. In at least one real
  deployment, Chromium crashed a few seconds after startup and that watchdog
  never logged anything, so nothing restarted it until `run.sh`'s own
  ~15s pgrep-based timeout gave up and exited the whole add-on. Added a CDP
  reachability health check (polls `/json/version` every 3s; 2 consecutive
  failures triggers the same escalate-to-software-GL-and-restart logic) that
  doesn't depend on that machinery at all, and also catches a still-running
  but unresponsive process, which process-exit detection could never catch
  regardless. The original process-exit watchdog is kept as a faster path
  for when it does fire.

## v1.4.1 - July 2026

- **Fix:** install the `dbus` package explicitly. It was previously pulled in
  transitively by Luakit; Chromium doesn't, so `dbus-daemon` (needed for the
  session bus and Onboard's dbus-send IPC) was missing after the v1.4.0 switch
- **Fix:** detect Chromium crashing *after* a successful startup (e.g. a
  GPU/EGL crash a moment after loading a page), which previously went
  unnoticed until `run.sh`'s browser-process check gave up and exited the
  whole add-on. A new watchdog task now catches this, escalates from
  hardware to software (SwiftShader) GL if the crash happened on hardware
  GL, and restarts - capped at 5 restarts per 3 minutes to avoid a crash
  loop if Chromium genuinely can't run in the environment
- **GPU acceleration:** added `--ozone-platform=x11` (pin the X11 backend
  explicitly rather than relying on auto-detection), `--disable-gpu-sandbox`
  (Chromium's GPU-process sandbox layer can fail to init under a container's
  restricted namespaces even with `--no-sandbox` set), and
  `--ignore-gpu-blocklist`/`--ignore-gpu-blacklist` (avoid Chromium's
  driver allow-list silently rejecting less common GPUs, e.g. Raspberry
  Pi's V3D) - aimed at making real hardware acceleration work reliably
  instead of always falling back to software rendering
- **Memory:** enabled the add-on's `tmpfs: true` option so Chromium's `/tmp`
  (used for shared-memory-like files via `--disable-dev-shm-usage`) is
  RAM-backed. Supervisor add-ons can't set Docker's `shm_size` directly, so
  this is the available way to give Chromium adequately-sized, fast shared
  memory instead of a small and/or disk-backed default - a common cause of
  Chromium renderer/GPU crashes in containers

## v1.4.0 - July 2026

- **Replaced Luakit with regular Chromium** as the kiosk browser
  - Chromium is launched with `--kiosk --app=<url>` plus a set of standard
    container-safe flags (`--no-sandbox`, `--disable-dev-shm-usage`, etc.)
  - Since Chromium has no in-process scripting hook (unlike Luakit's Lua API),
    all former `userconf.lua` behavior is now driven externally over the Chrome
    DevTools Protocol (CDP) by a new `chromium_kiosk.py` controller run inside
    `rest_server.py`: auto-login, HA sidebar/theme localStorage settings,
    unhandled-rejection suppression, HA websocket-recovery watchdog, periodic
    browser refresh, and restart-after-repeated-load-failures
  - Dark/light mode is applied via CDP `Emulation.setEmulatedMedia` (sets the
    `prefers-color-scheme` media query only, unlike Chromium's `--force-dark-mode`
    flag which would also recolor the page)
  - Zoom level is applied via `--force-device-scale-factor` at launch
  - Chromium automatically falls back from hardware (EGL) to software
    (SwiftShader) GL rendering if it fails to start with hardware acceleration
  - `launch_url` (REST API and gesture commands) now navigates the existing
    kiosk tab via CDP instead of spawning a second browser process, so the
    `unique_instance.lua` patch is no longer needed
  - Removed `userconf.lua` and `unique_instance.patch`; removed `luakit` from
    the default `command_whitelist`
  - Added `cdp_client.py` (shared CDP helper) and `chromium_kiosk.py` (the
    Chromium kiosk controller)

## v1.3.2 - April 2026

- Added explicit BUILD_FROM location to Dockerfile for ha core 2026.04+

## v1.3.1 - April 2026

- Updated auto-login JS injection in 'userconf.lua' for 2026.4+
- Fixed whitelist logic to allow commands outside of default path

## v1.3.0 - February 2026

- Added more key bindings for opening/closing/rotating tabs and windows
- Add x11vnc server to facilitate remote viewing or debugging of kiosk
- Added 'screenshot' function to REST_API and gesture action commands
- Added `enable_inputs` and `disable_inputs` functions to REST_API to allow
  locking down (and unlocking) inputs by disabling keyboard, mouse and
  touch functions
- Added `mute_audio`, `unmute_audio` and `toggle_audio` functions to
  REST_API to change audio state (`toggle_audio` can also be used in
  gesture action commands)
- Converted default gestures in `config.yaml` to use internal
  `kiosk.<function>` handlers rather than calling shell functions
- Added short list of built-in keyboard shortcuts
- Revamped `ultrasonic-trigger.py` example and added new functionality to
  enable/disable inputs, mute/unmute audio, and rotate through a list of
  URLs
- Added INSTRUCTIONS section to README.md (thanks: @cvroque)
- Added more details to README.

## v1.2.0 - January 2026

- Added ability to set HA theme in config.yaml
- Added USB audio (`audio: true` and `usb: true` in config.yaml) Added
  corresponding config option `audio_sink` which can be: auto, hdmi, usb,
  or none.
- Increased ulimit (in config.yaml) to reduce crashes from heavy usage
- Improved browser refresh logic and stability by:
  - Changing browser refresh from JS injection to native luakit view:reload
  - Forcing hard reload (including cache) every HARD_RELOAD_FREQ reloads
    (refreshes)
  - Killing and restarting luakit if ang page fails to reload more than
    MAX_LOAD_FAILURES in a row
- Improved logging of browser refresh
- Added luakit memory process logging after every page load
- Added JS injections to protect against browser errors & crashes
- Improved robustness and debug output for associating udevadm paths with
  libinput list devices
- Changed run.sh exit logic so that quits if no luakit process for at least
  10 seconds (even if original luakit process has exited)
- Removed config.yaml parameter `allow_user_command` and replaced with
  `command_whitelist` regex. Also added internal whitelist, blacklist, and
  dangerous shell tokens list along with path restrictions (see README.md)
  for details on how behavior has changed.
- Wrote complete Python 'xinput2' parser to detect broad range of mouse and
  touch gestures and execute gesture-specific commands. Replaces prior very
  limited tkinter implementation. See 'mouse_touch_inputs.py' and
  'gesture_commmands.json'
- Added corresponding 'gestures' list option to config.yaml
- Added 'Option "GrabDevice" "true"' to keyboard InputClass section in
  xorg.conf
- Added mouse buttons (left/right/middle/drag) to default Onboard keyboard
  layout
- Refactored and rewrote `rest_server.py`
- Added `REST_IP` to options to allow users to set the listening IP address
- Changed onscreen_keyboard option default to `true`
- README edits

## v1.1.1 - September 2025

- Auto-detect drm video card used and set 'kmsdev' accordingly in xorg.conf
- Added more system & display logging
- Minor bug fixes and tweaks

## v1.1.0 - September 2025

- Added REST API to allow remote launching of new urls, display on/off,
  browser refresh, and execution of one or more shell commands
- Added onscreen keyboard for touch screens (Thanks GuntherSchulz01)
- Added 'toogle_keyboard.py' to create 1x1 pixel at extreme top-right to
  toggle keyboard visibility
- Save DBUS_SESSION_BUS_ADDRESS to ~/.profile for use in other (login)
  shells
- Code now potentially supports xfwm4 window manager as well as Openbox
  (but xfwm4 commented out for now)
- Revamped 'Xorg.conf.default' to use more modern & generalized structure
- Prevent luakit from automatically restoring old sessions
- Patched luakit unique_instance.lua to open remote url's in existing tab
- Force (modified) passthrough mode in luakit with every page load to
  maximize kiosk-like behavior and hide potentially conflicting command
  mode
- Removed auto refresh on display wake (not necessary)

## v1.0.1 - August 2025

- Simplified and generalzed libinput discovery tagging and merged resulting
  code into 'run.sh' (Thanks to GuntherSchulz01 and tacher4000)
- Added "CURSOR_TIMEOUT" to hide cursor (Thanks tacher4000)
- Set LANG consistent with keyboard layout (Thanks tacher4000)
- Added additional logging to help debug any future screen or input (touch
  or mouse) issues
- Substituted luakit browser-level Dark Mode preference for HA-specific
  theme preference (Thanks tacher4000)

## v1.0.0 - July 2025

- Switched from (legacy) framebuffer-based video (fbdev) to OpenGL/DRI
  video
- Switched from (legacy) evdev input handling to libinput input handling
- Switched from "HDMI PORT" to "OUTPUT NUMBER" to determine which physical
  port is displayed
- Added 'rotation' config to rotate display
- Added boolean config to determine whether touch inputs are mapped to the
  display output (in particular, this will rotate them in sync)
- Modified 'xorg.conf' for consistency with 'OpenGL/DRI' and 'libinput'
- Attempted to maximize compatibility across RPi and x86
- Added ability to append to or replace default 'xorg.conf'
- Added ability to set keyboard layout. (default: 'us')
- Updated & improved userconf.lua code
- Extensive changes and improvements to 'run.sh' code
- Added back (local) DBUS to allow for inter-process luakit communication
  (e.g., to allow use of unique instance)

## v0.9.9 - July 2025

- Removed remounting of /dev/ ro (which caused HAOS updates to fail)
- Added 'debug' config that stops add-on before launching luakit
- Cleaned up/improved code in run.sh and userconf.lua
- Reverted to luakit=2.3.6-r0 since luakit=2.4.0-r0 crashes (temporary fix)

## v0.9.8 – June 2025

- Added ability to set browser theme and sidebar behavior
- Added <Control-r> binding to reload browser screen
- Reload browser screen automatically when returning from screen blank
- Improved input validation and error handling
- Removed host dbus dependency
- Added: ingress: true
- Tightened up code
- Updated documentation

## v0.9.7 – April 2025

- Initial public release
- Added Zoom capability

## 0.9.6 – March 2025

- Initial private release
