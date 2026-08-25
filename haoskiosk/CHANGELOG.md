# Changelog

## v1.4.32 - August 2026

- **Improvement: `voice_satellite_entity` accepts what HA's own pickers show.** Instead of
  requiring the raw entity id, it now also accepts:
    - the exact friendly name (e.g. 'Home Assistant'), resolved case-insensitively against
      HA's state machine at startup via the Supervisor-proxied core API;
    - 'auto' = first assist satellite found.
  Full entity ids keep working as before; ambiguous or unmatched names log a warning and fall
  back to manual selection.

## v1.4.31 - August 2026

- **Fix: `voice_satellite_entity` now binds on first load.** The very first document usually
  finished loading *before* the kiosk's CDP session registered its before-page-scripts
  injection - and since HA is an SPA (no new document until the periodic refresh), the seed
  only landed up to 10 minutes later. The auto-start loop now seeds the configured entity
  right after each page load as a catch-up; Voice Satellite's own engine re-checks localStorage
  every second, so it binds within seconds of boot with no reload.
- New diagnostic: if the card keeps clearing the configured entity, the log says so explicitly -
  that means the id doesn't match an existing assist_satellite entity in HA (typo), not a kiosk bug.

## v1.4.30 - August 2026

- **New: `voice_satellite_entity` option** - pre-select the assist-satellite entity the Voice
  Satellite panel binds to (e.g. 'assist_satellite.sat_office'), seeded into the page before
  any of its scripts run on every load. Without it, each restart wipes the browser profile
  (and with it the card's localStorage pick), so someone had to open the panel and re-select
  the satellite by hand after every boot.
- Malformed ids are rejected at startup with a log warning instead of being silently cleared
  by the card later.

## v1.4.29 - August 2026

- **Fix: add-on failed to start at all (`SyntaxError: unmatched '}'`).** A stray closing brace
  left over from v1.4.28's `media_mic` → `media_stream_mic` rename broke `chromium_kiosk.py`
  compilation, crashing the container on every launch (voice satellite auto-start included).
- The mic preference key itself stays `media_stream_mic` (Chromium's own Preferences spelling,
  via GetPreferenceName()); test updated to match.

## v1.4.28 - August 2026

- **Fix: no more 'Save password?' bubble after auto-login, and no microphone permission prompt
  even on a fast first page load.** Both prompts are things a headless kiosk can never answer,
  and both are now silenced by seeding Chromium's `Default/Preferences` into the fresh profile
  before every launch (the profile is wiped each time, so the seed is deterministic):
    - password manager fully off (`credentials_enable_service`, `credentials_enable_autosignin`,
      `profile.password_manager_enabled`) - kills the save-password bubble that the HA
      auto-login's form fill used to trigger;
    - with `voice_satellite` on, a mic content-setting exception (`media_mic` → ALLOW) for
      exactly the HA origin - read by Chromium before any page exists, so it wins the race
      that CDP `Browser.grantPermissions` could occasionally lose against Voice Satellite's
      own getUserMedia call (the grant stays as belt-and-suspenders).

## v1.4.27 - August 2026

- **New: `voice_satellite` (default `false`) - hands-free auto-start of
  [Voice Satellite](https://github.com/jxlarrea/voice-satellite-card-integration)**, the HACS
  integration that turns any browser into an `assist_satellite` with wake-word detection.
  Until now, every kiosk boot/reload parked Voice Satellite behind its floating "tap to start"
  button (browsers refuse mic capture without a user gesture, and the Chromium profile is wiped
  on each launch so a persisted "allow" never survives). With this option on, the add-on now:
    - pre-grants microphone access for the HA origin via CDP `Browser.grantPermissions`
      (`audioCapture`) right after launch, before the first page finishes loading;
    - passes `--unsafely-treat-insecure-origin-as-secure` when `ha_url` is plain http, so
      `getUserMedia` exists at all without HTTPS (browser-side equivalent of the official
      Android Kiosk Satellite app's secure-context loopback proxy);
    - watches each page load for Voice Satellite's floating start button and taps it via CDP
      `Input.dispatchMouseEvent` (a *trusted* input event carrying real user activation, which
      a JS `.click()` would not be) - up to 3 taps within 45s per load, then logs and gives up.
  Result: wake word comes up by itself on boot, after periodic refreshes, and after
  websocket-recovery reloads. Requires the Voice Satellite integration installed in HA and its
  satellite entity assigned to this browser in the sidebar panel (one-time setup). Note the
  existing screen-off tradeoff still applies: with `pause_on_screen_off` or a non-zero
  `screen_timeout`, the frozen page stops hosting the wake-word engine too - keep the screen
  always on for always-listening voice; the add-on logs a warning when both are combined.

## v1.4.26 - August 2026

- **Fix (bug): `/health` and unknown REST routes returned HTTP 500** — the security middleware
  did `getattr(handler, "cmd_name")` for every route, but the `/health` lambda and the catch-all
  404 handler have no such attribute, so they raised `AttributeError` → 500 instead of 200/404.
  Now `getattr(handler, "cmd_name", None)` — `/health` returns 200 and unknown routes return 404.
- **Security: don't trust `X-Forwarded-For` for the localhost check** — the previous fallback was
  spoofable ("X-Forwarded-For: 127.0.0.1") and could bypass the protected-command gate when
  `request.remote` is unset. Only the real TCP peer is trusted now.
- **Dropped all 32-bit arches (`armhf`, `i386`, `armv7`)**: the current official Home Assistant
  builder (`home-assistant/builder/actions/*`) only cross-builds `amd64` + `aarch64`, HA is
  deprecating 32-bit, and Alpine has no Chromium for armhf/i386. Supported: `aarch64`, `amd64`.
- **New CI/CD (.github/workflows) + prebuilt images**: `build.yml` publishes prebuilt multi-arch
  images to `ghcr.io` on push to main using the current composable builder actions — `config.yaml`
  now sets `image:` so users no longer compile on install. `ci.yml` runs the test suite +
  pre-commit (codespell/shellcheck/safety checks) on push/PR.
- **New `watchdog`** pointing at `/[HOST]:[PORT:8080]/health` so the Supervisor restarts the
  add-on if the REST server hangs (requires `/health` fix above).
- Removed the broken `sync-readme-jjk` pre-commit hook (called a nonexistent GitHub workflow) and
  the no-op `exclude: ^$`.
- `cdp_client.py`: `asyncio.get_event_loop()` → `get_running_loop()` (deprecated).
- `translations/en.yaml`: fixed `docker -exec` → `docker exec` typo.
- `run.sh`: aligned debug fallback defaults with config.yaml (`SCREEN_TIMEOUT 0`,
  `ONSCREEN_KEYBOARD true`).
- tests/requirements-test.txt now includes `aiohttp` and `python-xlib` (module imports needed by
  the suite).

## v1.4.25 - August 2026

- **New: `pause_on_screen_off` (default `true`) freezes the Chromium page while the physical
  screen is DPMS-blanked**, so it stops all rendering/compositing/JS-timer work for a page
  nobody can see - previously `display_off`/`screen_timeout` only cut the monitor's power
  signal (`xset dpms force off`); Chromium kept compositing the dashboard at full rate the
  entire time regardless. Implemented as a new `ChromiumKiosk._dpms_watch_loop` that polls
  `xset -q` every 5s (needed since `screen_timeout` blanks the screen via the X server's own
  idle timer without ever calling into this add-on - REST-hooking `display_on`/`display_off`
  alone would miss that, the most common case) and, on a detected transition, calls CDP's
  `Page.setWebLifecycleState` - `"frozen"` on screen-off, `"active"` + a forced `Page.reload`
  on screen-on. The forced reload on wake (rather than trusting the frozen page's websocket to
  resume) is a deliberate tradeoff: freezing stops Home Assistant's own frontend connection
  from being serviced too, so a graceful resume isn't realistic - but unlike a momentary
  screensaver overlay, this add-on's "screen off" routinely lasts minutes to hours, so a one-
  time ~1-2s reload flash on wake is a good trade for near-zero rendering work for however
  long the screen stays off. `kiosk_status` now reports `page_frozen`. Idea and the underlying
  technique (stop rendering behind something covering the dashboard, unlike freezing a
  background browser *tab*) both credited to
  [jxlarrea/kiosk-satellite](https://github.com/jxlarrea/kiosk-satellite)'s "Pause dashboard
  during screensaver" optimization, adapted here for a DPMS-driven screen (not an in-page
  overlay) and a CDP-driven browser (not a native Android WebView) - see that project's
  `docs/optimizations.md` for the original measurements (screensaver case: 152%→57% app CPU,
  130%→35% renderer CPU, 70%→0% GPU, -20°C).
- Added `haoskiosk/tests/test_dpms_parsing.py`: unit tests for the `xset -q` output parser and
  the freeze/unfreeze reaction logic (mocked CDP connection), including that a CDP failure is
  swallowed rather than destabilizing anything else.
- config.yaml: `output_number`'s schema was `int(1,2)`, silently rejecting any value above 2
  even though `run.sh` already supports and gracefully falls back for any number of connected
  video outputs. Changed to `int(1,)`.
- `translations/en.yaml`: the REST Bearer Token option's translation was filed under the stale
  key `rest_authorization_token` instead of `rest_bearer_token` (`config.yaml`'s actual key),
  so it showed up untranslated (raw key name) in the HA Configuration UI. Fixed the key.
- `examples/ultrasonic-trigger.py`: `ha_launch_url()` checked `data["result"]["stdout"]` for a
  `"Monitor is On"` string, but `/launch_url` only ever returns `{"success": bool}` - no
  `"result"` key at all. The check was therefore always `False` regardless of actual success,
  silently breaking this example's dashboard-restore-on-exit and URL-rotation logic. Fixed to
  check the real response shape.
- Both `README.md`s rewritten for scannability: the add-on's own README (the full reference)
  went from ~1045 to ~320 lines - configuration options, the REST API, and the gesture grammar
  are now compact tables instead of one prose subsection per item, and the long
  `configuration.yaml` `rest_command:` reference block moved into a collapsed `<details>`
  section. No reference content was removed. Also fixed a broken self-referential link
  (`haoskiosk/CHANGELOG.md` from a file that's already inside `haoskiosk/`) and two config
  table rows that named the wrong option keys.

## v1.4.24 - August 2026

**Performance: avoid building (and immediately discarding) debug-log strings on the per-input-
event hot path** (`mouse_touch_inputs.py`'s `process_PRESS`/`process_RELEASE`/`process_MOTION`
and the raw `xinput` stanza parser in `XInputParser.__next__`). `debug(level, msg)` takes an
already-formatted string, so every call site previously built its f-string (for `process_MOTION`
on `TOUCH` devices, including a `ContactGroup` registry lookup) unconditionally, even at the
default `DEBUG_LEVEL=0` where the result is immediately thrown away. Every such call site is now
guarded with an explicit `if DEBUG_LEVEL >= N:` check first.

Verified this is a pure performance change with no behavior difference: a new test
(`tests/test_event_processing.py`, `TestDebugLevelDoesNotAffectOutcome`) drives synthetic
press/release events through the same code path at `DEBUG_LEVEL` 0/2/4/5 and asserts the
resulting `ContactGroup`/`GestureSequence` state is identical at every level, while debug output
still appears whenever the level actually calls for it (i.e. the added guards can't have silently
suppressed real debug output either). A microbenchmark (200k synthetic `MOTION` events on a
`TOUCH` device at `DEBUG_LEVEL=0`) measured the eliminated `ev.sprint()` overhead at ~3.4us/event
- real, but modest in absolute terms; the point was removing needless work from the highest-
frequency path in the program, not a specific throughput target.

Considered and *not* done this round: batching the multiple `pactl` subprocess calls
`/mute_audio`/`/unmute_audio`/`/toggle_audio` each make in `rest_server.py` into fewer process
spawns. Those are rare, REST-triggered calls (not per-event), so the realistic win is negligible
next to the risk of changing already-working, security-sensitive command execution code for
little benefit - skipped. Also explicitly *not* touched: any Chromium GPU/rendering flags (e.g.
reassessing whether `--disable-accelerated-2d-canvas` - added for a Chromium 136 Skia bug - is
still needed now that v1.4.22 bumped to 150.x, or trying a Vulkan-based ANGLE backend). Those
need validation on real kiosk hardware that isn't available in this environment; shipping an
unverified change to that code is exactly the kind of mistake this changelog's own v1.4.6 and
v1.4.12 entries document taking real device debugging to catch and fix.

## v1.4.23 - August 2026

Follow-up patch: a self-audit of v1.4.22's own changes found one more real security bug (in
the fix that commit itself added) plus a few smaller correctness gaps, and separately closes
two known gesture-grammar gaps that were previously tracked as an `xfail` test.

- **Security fix: the `SAFE_REDIRECT_REGEX` fix added in v1.4.22 could itself be bypassed.**
  None of its `/dev/null` alternatives required a trailing word boundary, so
  `"echo secret > /dev/nullbackup"` matched the "safe" `> /dev/null` case as a *prefix* and was
  stripped before the unsafe-redirect check ran - leaving a redirect to an attacker-chosen
  filename (`/dev/nullbackup`, not `/dev/null`) undetected, using only a whitelisted program.
  Verified end-to-end before fixing. Added a boundary requiring what follows `/dev/null` to be
  whitespace, a shell separator, or end-of-string; also tightened `2>&1`/`1>&2` the same way so
  they can't be a prefix of a longer, different fd number like `2>&15`.
- **Fix: a `ContactGroup` recovered by the new idle-timeout (v1.4.22) could leave an orphaned
  `GestureSequence` behind.** If the *first* click of a would-be double-click completed normally
  (registering a `GestureSequence` and queuing its closeout) but the *second* click's RELEASE was
  then dropped, the idle timer correctly force-cleared the stuck `ContactGroup` but never touched
  the still-registered `GestureSequence` - every subsequent completed click on that device kept
  appending onto it, growing it unboundedly and permanently miscounting N-click gestures. The idle
  timeout now also discards any `GestureSequence` registered for the same device.
- **Fix: one `asyncio.create_task()` call inside `_launch_process`'s hardware→software GL fallback
  loop was missed by v1.4.22's `_spawn()` hardening.** The loop reassigns `self._stderr_task` on
  each retry attempt, which could drop the only reference to the *previous* attempt's still-live
  stderr-reader task - the exact GC-mid-execution hazard `_spawn()` was added to close, in the one
  spot inside the very code path that commit hardened. Now uses `_spawn()` too.
- **Fix: `mouse_touch_inputs.py` always exited 0, even on an unhandled exception**, since its
  top-level handler printed a traceback but never called `sys.exit()`. This made v1.4.22's new
  `run.sh` supervisor log a misleading "exit code 0" for a genuine crash (the restart itself still
  happened correctly - only the logged diagnostic was wrong). Now exits 1.
- **Fix: `Button`/`Finger` gesture strings, README-documented directional `DRAG_LEFT`/etc., and
  undirected `SWIPE` (as a wildcard for its directional variants) didn't fully work for `TOUCH`
  devices.** `DeviceType.TOUCH`'s `gestures` dict was missing entries for bare `SWIPE` and all four
  directional `DRAG_*` `GestureType`s - since the gesture-string regex, per-device validation, and
  `classify_click()`'s own gating are all derived from that same dict, this blocked both parsing
  `"3_TOUCH_1_SWIPE"`/`"2_TOUCH_1_DRAG_LEFT"` as config keys *and* ever detecting those as the
  actual runtime-classified gesture. Added the five missing entries; the `xfail` test from v1.4.22
  is now a normal passing test, plus a new test locks in that a configured bare-`SWIPE` rule
  matches an actually-detected `SWIPE_LEFT`/etc. event (already worked via `matches_rule()`'s
  existing `GestureType.base_type` wildcard logic - this was purely a parsing-stage gap).
- Minor README fix: "HA Theme" listed its default as `True` (a copy-paste leftover from the "Dark
  Mode" section above it) - the actual default is `""` (unset, governed by `DARK_MODE`).

## v1.4.22 - August 2026

Security/robustness/maintainability sweep across the REST API, the Chromium/CDP controller,
and the gesture-input parser, plus new test coverage and a Chromium version bump. No behavior
changes to documented, working functionality - only fixes to bugs and gaps found by review.

- **Security fix: command whitelist/blacklist could be bypassed via an embedded newline**, in
  both `rest_server.py`'s `/run_command`/`/run_commands` REST endpoints and
  `mouse_touch_inputs.py`'s gesture action commands. `SEPARATORS` (the set used to split a
  compound command into individual programs to check) didn't include `\n`/`\r`, so a command
  string like `"echo hi\nrm -rf /media"` had only its first line's program ("echo") checked
  against the whitelist/blacklist, while the *entire* string - newline included - was still
  handed to `/bin/sh -c`, which treats a literal newline exactly like `;`, running the second,
  unchecked (here, blacklisted) command anyway. Added `\n`/`\r` to `SEPARATORS` in both files so
  every line is tokenized and checked independently, closing the bypass.
- **Security fix: whitelisted commands could redirect output to write/overwrite arbitrary
  files.** `SAFE_REDIRECT_REGEX` (meant to restrict shell redirection to safe forms like
  `> /dev/null`, `2>&1`) was defined but never actually enforced anywhere, so e.g.
  `"echo pwned > /etc/some_file"` passed whitelist checking (only `echo`'s program name was
  checked) and executed the redirection unimpeded. Now enforced in both files' command
  validators: any redirection operator not matching one of the explicitly-safe forms blocks the
  whole command. (Fixing this also surfaced and fixed a related latent bug: splitting on a bare
  `&` for background-job detection was mis-tokenizing legitimate `2>&1`-style fd-merge
  redirections, treating the trailing `1` as an unknown "program" and rejecting the command -
  safe redirections are now stripped before program-name extraction.)
- **Security fix: REST API bearer token compared with a non-constant-time `!=`**, a timing
  side-channel that could in principle help an attacker recover `REST_BEARER_TOKEN` byte by
  byte over many requests. Switched to `hmac.compare_digest`.
- **Fix: `POST /unmute_audio` raised an opaque 500 instead of its documented error response**
  whenever the underlying `pactl` calls failed (e.g. no default sink) - `volumes` was only ever
  assigned inside a success branch but read unconditionally in the response, raising
  `NameError`. Now initialized up front.
- **Fix: a blocking `subprocess.run()` call inside an `async def` handler** (`get_input_devices`,
  used by `/disable_inputs` and `/enable_inputs`) stalled the entire REST server's event loop -
  every other in-flight request (screenshots, `kiosk_status`, `run_command`, ...) - for however
  long `libinput list-devices` took. Switched to `asyncio.create_subprocess_exec`.
- **Fix: race condition in `/disable_inputs`/`/enable_inputs`** could let two concurrent calls
  both "see" a device as not-yet-grabbed and both spawn a grabbing `evtest --grab` process on the
  same `/dev/input/eventN`, leaving one orphaned and the device unexpectedly still blocked after
  a later `/enable_inputs`. Serialized both handlers with a lock; also registered the long-lived
  `evtest` processes in the same tracking set `/current_processes` already reports, since they
  weren't counted before.
- **Fix: REST API 500 responses leaked raw exception text** (paths, argument values, internals)
  to the client, including unauthenticated callers when `REST_BEARER_TOKEN` isn't set. The detail
  is already logged server-side; the client now just gets a generic error message.
- **Robustness: several fire-and-forget `asyncio.create_task()` calls in `chromium_kiosk.py`**
  (browser restarts, auto-login, post-load hooks) held no reference beyond the event loop's own
  weak one - per asyncio's own documented gotcha, an in-flight task can be garbage-collected at
  any await point with no reference keeping it alive. All are now tracked via a small `_spawn()`
  helper that keeps a strong reference and logs any exception the task raises (previously silent).
- **Robustness: a Chromium restart that failed partway through (e.g. the CDP connection failing
  right after Chromium's HTTP endpoint came up) could leave the controller with a half-configured
  `self.conn`** and no event handlers registered, while the health check - which only polled HTTP
  reachability - kept reporting "healthy" indefinitely, since Chromium itself was still up. The
  health check now also verifies the CDP reader task is actually alive, and a failed restart now
  cleans up fully (closes/nulls the connection, kills the process) so the next health-check cycle
  correctly detects it's down and retries, still bounded by the existing restart-rate-limiter.
- **Robustness: the hardware-GL retry loop could permanently get stuck on software (SwiftShader)
  rendering.** It previously cleared its own "currently forced onto software GL" bookkeeping
  *before* confirming the retry attempt actually ran (it could be a silent no-op if a concurrent
  restart already held the restart lock) - leaving the loop's own guard (which requires that
  bookkeeping to be set) permanently disabled with no further retry ever attempted. The retry
  attempt now explicitly tries hardware GL for just that one attempt without touching the
  persistent flag until the outcome (success, or fallback to software within the same attempt) is
  known.
- **Robustness: unbounded waits that could wedge the restart machinery.** `_kill_process()`'s
  post-SIGKILL `proc.wait()` and the per-page-load `dbus-send` call (Onboard hide) had no
  timeout; since `_kill_process()` runs under the same lock every restart path needs,  a stuck
  reap would silently block every future restart trigger indefinitely. Both are now bounded, and
  `stop()` now awaits every cancelled background task to actually finish (not just requests
  cancellation) before tearing down the rest of the controller's state.
- **Robustness: `ZOOM_LEVEL` was only clamped on the low end** (`max(value, 1)`), so a
  misconfigured value had no upper bound on the resulting `--force-device-scale-factor`. Now
  clamped to a sane range (25-500) with a warning log, consistent with how other options validate.
- **Robustness: a CDP connection that dropped mid-request left any in-flight `send()` call
  waiting for its full timeout** instead of failing immediately, adding needless latency to
  crash/restart detection. `CDPConnection`'s read loop now fails all pending requests as soon as
  the connection ends.
- **Robustness: a single dropped touch/mouse RELEASE event (or an `xinput` crash/restart) could
  permanently corrupt gesture recognition for a device.** `xinput test-xi2 --root` is one process
  covering *every* input device, so a restart previously left whatever contact groups were mid-
  press for any device registered forever, silently absorbing every future press
  (`_reset_all_gesture_state()` now clears all devices' state on restart). Separately, a
  `ContactGroup` that never received a RELEASE for one of its contacts (a real, documented touch-
  event reliability quirk) never completed and so was never replaced, permanently wedging that
  device the same way; each `ContactGroup` now force-clears itself after 30s with no RELEASE.
- **Fix: `<DEVICE>` "Mechanism" aliases (`Button`, `Finger`) documented in the README's Gesture
  String Keys section didn't actually work** - e.g. the README's own example
  `"2_Button_1_Long Click"` failed to parse. The gesture-string regex only ever matched
  `DeviceType` enum names (`MOUSE`/`TOUCH`/...), never each device's `contact_type` alias. Added
  a reverse lookup so both forms resolve to the same `DeviceType`.
- Removed the dead `-w`/`--white_list` CLI flag in `mouse_touch_inputs.py` - `run.sh` passed it,
  but the script never read it; the actual whitelist has only ever come from the
  `COMMAND_WHITELIST` environment variable (which `run.sh` also exports), so the flag did nothing
  and risked confusing anyone updating the whitelist mechanism later.
- **`run.sh` now supervises `mouse_touch_inputs.py`**, restarting it (rate-limited) if it exits
  unexpectedly. Previously it was backgrounded with no PID tracking or restart logic at all -
  unlike the REST server, whose exit `run.sh` explicitly waits on - so a crash (its own top-level
  handler just prints a traceback and exits) silently disabled all gesture/touch control for the
  rest of the container's life with no signal at this level.
- **Chromium bumped from Alpine v3.21's `community` repo (136.0.7103.113) to v3.24's
  (150.0.7871.181)** - `BUILD_FROM`'s default (`ghcr.io/home-assistant/base`) already moved its
  own `latest`/default Alpine version to 3.24, so the previous pin meant Chromium itself was
  quietly running ~14 major versions behind the rest of the image's own packages, missing
  everything patched upstream since. `BUILD_FROM` is now pinned to the matching `:3.24` tag
  (rather than the floating `:latest`) so the two can't silently drift apart again, and for
  reproducible builds generally.
- **`ARG BUILD_VERSION`/`ENV ADDON_VERSION` moved to after the (expensive, rarely-changing)
  package-install layer** in the Dockerfile. It previously sat before `RUN apk add`, and since
  `BUILD_VERSION` changes on every add-on release, Docker's layer caching invalidated the entire
  Xorg/Chromium install on every single release - even one that only touched `run.sh` or a
  Python file.
- **Added a pytest suite** (`tests/test_gesture_grammar.py`) for `mouse_touch_inputs.py`'s
  gesture-string grammar (`RangeNumber`'s subset/superset comparison semantics,
  `GestureCommand`'s key/value parsing against the README's own documented examples), the command
  whitelist/blacklist/redirection security model (including regression tests for the two security
  fixes above), and the gesture-commands-file preprocessor (comments, brace-optional/trailing-
  comma JSON, the `"gestures"` wrapper key format used to read HA's `options.json` directly).
  There was previously no test coverage anywhere in the repo for this fairly involved, hand-
  written parsing logic - a regression here would otherwise be very hard to trace back to a
  parsing bug from a user report of "gesture X doesn't work anymore". One gap surfaced by writing
  these tests is tracked as an `xfail`: the README documents directional `DRAG_LEFT`/etc. gesture
  strings as valid, but no `DeviceSpec` currently defines those gesture names, so none can
  actually parse - flagged rather than fixed outright since fixing it for real touches gesture
  *classification*, not just string parsing, and needs hardware verification.

## v1.4.21 - July 2026

- **Fix: `browser_language` had no effect on Chromium's own native UI**
  (dialogs like the "Save password?" prompt, spell-checker, etc.) - only on
  web-page content. Root cause, confirmed inside the running container: the
  Alpine `chromium` package ships **only the `en-US` locale** -
  `/usr/lib/chromium/locales/` had a single `en-US.pak` and nothing else, so
  `--lang=es-ES` had no other locale data to load and silently fell back to
  English for every native string, regardless of the configured
  `browser_language`. Added the separate `chromium-lang` Alpine package
  (kept version-pinned alongside `chromium`/`chromium-swiftshader` on the
  same `v3.21/community` repo), which provides the full set of locale
  `.pak` files Chromium ships upstream

## v1.4.20 - July 2026

- **Fix: `browser_language` didn't change `navigator.language`.** Verified on a
  real device: `Emulation.setLocaleOverride` only changes
  `Intl.*.resolvedOptions().locale` on this Chromium build - `navigator.language`
  and `navigator.languages` stayed whatever Chromium's untouched default was,
  even immediately after a full `ignoreCache` reload. Home Assistant's own UI
  language (`hass.language`) is unaffected by this - it comes from the logged-in
  user's profile/localStorage, not `navigator.language` - so HA dashboards
  already displayed correctly in the configured language; this only mattered
  for other sites opened through the kiosk (e.g. the `Open Google search`
  gesture command), which typically *do* read `navigator.language` directly.
  Added a `navigator`/`navigator.languages` override via
  `Object.defineProperty` on `Navigator.prototype`, injected with
  `Page.addScriptToEvaluateOnNewDocument` (same mechanism already used for the
  error-suppression and websocket-recovery scripts) so it's guaranteed to run
  before any page script, on every navigation

## v1.4.19 - July 2026

- **New: `browser_language` option to choose the Chromium/HA frontend
  language.** Previously there was no way to control the displayed
  language at all - `keyboard_layout`'s "and language" wording was
  aspirational only: it sets the xkb key layout and (via `run.sh`)
  exports `LANG` to that same keyboard code (e.g. `LANG=us`), which isn't
  a real locale and has no effect on Chromium or HA's displayed language.
  Added a real `browser_language` config option (BCP-47 code, e.g.
  `es-ES`, `fr`, `pt-BR`; blank = Chromium's default `en-US`) that:
  launches Chromium with `--lang=<code>` (drives its own UI/spellchecker
  locale); overrides the subprocess `LANGUAGE` env var so it can't be
  shadowed by the unrelated keyboard-layout-derived `LANG`; and, over
  CDP, applies `Emulation.setLocaleOverride` (`navigator.language`/`Intl`)
  plus a matching `Accept-Language` header via
  `Network.setExtraHTTPHeaders` - together, what Home Assistant's own
  frontend auto-detection reads whenever the user hasn't set a language
  on their HA profile. Applied once per Chromium session (initial connect
  and after every restart), consistent with how dark-mode emulation is
  already handled

## v1.4.18 - July 2026

- **Fix: canvas graphics silently rendering wrong (not just slowly).**
  Skia's GPU-accelerated Canvas2D backend miscompiles some composite
  operations on the GPUs these boards ship with - verified on a Raspberry
  Pi's V3D through ANGLE: a path built from zero-radius arcs plus a
  ~358-degree sweep, filled under
  `globalCompositeOperation = 'destination-out'`, erases far more than the
  path actually covers. Real-world victim: the Material You panel's colour
  disk, which builds its wheel by accumulating 360 two-degree wedges cut
  out exactly that way - it rendered as a handful of stray radial lines
  instead of a colour wheel. Diagnosed by reading the canvas back with
  `toDataURL` (the pixels are already wrong before compositing ever
  happens, ruling out a display/layering issue) and then re-running the
  exact same drawing code on the device under different flags: broken with
  `--use-gl=angle` alone, byte-identical to the software renderer once
  Canvas2D acceleration is off. Added `--disable-accelerated-2d-canvas`.
  Only Canvas2D moves back to the CPU - GPU compositing, rasterization and
  WebGL all stay hardware-accelerated, so the v1.4.1/v1.4.6/v1.4.12 GPU
  work is unaffected. The trade-off is that canvas-heavy content (HA's
  history charts) is painted on the CPU, which is a fair price for not
  silently drawing the wrong thing

## v1.4.17 - July 2026

- **Fix: the onscreen keyboard never popped up when tapping a text field**
  (the actual root cause, diagnosed on a live device rather than inferred
  from symptoms). Onboard's auto-show - the only way the keyboard ever
  appears by itself - works by watching AT-SPI for a focused editable
  node. Chromium *does* register on the AT-SPI bus, which is why every
  check we'd built so far looked healthy, but without
  `--force-renderer-accessibility` it exposes only its own browser UI:
  the entire renderer-side tree is absent, so every text field on the
  dashboard is invisible to accessibility clients and nothing Onboard
  watches ever changes. Walking the AT-SPI tree from Chromium's
  application node on the running add-on returned exactly one `[frame]`
  whose children were all null. Added the flag (only when
  `ONSCREEN_KEYBOARD` is enabled - maintaining the renderer a11y tree
  costs CPU/memory on every DOM update, not worth paying otherwise).
  Verified end-to-end on the device: with the flag, Onboard logs the
  focused node as `role=ENTRY state=[EDITABLE, FOCUSED, ...]` and the
  keyboard auto-shows immediately over the fullscreen kiosk window.
  This is what Luakit gave us for free upstream - GTK apps expose their
  a11y tree through the ATK bridge by default - and what silently went
  missing in the switch to Chromium
- **Corrects the v1.4.4 note** claiming AT-SPI-based auto-show wasn't
  feasible because "Alpine only packages the AT-SPI registry daemon, not
  the GTK/ATK bridge". That was wrong: `libatk-bridge-2.0` is present and
  the accessibility bus (`at-spi-bus-launcher`, `at-spi2-registryd`) is
  already running fine inside the add-on container - the missing piece
  was always on Chromium's side. The v1.4.4 Openbox `above`-layer rule is
  still correct and still needed (confirmed by screenshot: without it the
  keyboard would render below the `--kiosk` window); it just wasn't
  sufficient on its own

## v1.4.16 - July 2026

- Reverted the Virtual Keyboard Plus extension force-install (v1.4.14,
  v1.4.15) at the user's request. Back to Onboard only for
  `ONSCREEN_KEYBOARD` - its D-Bus service is confirmed registering
  correctly (v1.4.13), so toggle/hide (gesture, REST, Ctrl+Alt+o) should
  work; if the keyboard still doesn't show, the next thing to check is
  window visibility/focus rather than the D-Bus IPC or an extension
  install

## v1.4.13 - July 2026

- The onscreen keyboard reportedly never appears under any circumstance
  (not even manual toggle via gesture/REST/Ctrl+Alt+o, not just auto-show)
  on at least one device. That points at something more fundamental than
  the v1.4.4 window-layering fix - most likely Onboard never actually
  registering the `org.onboard.Onboard` D-Bus service that every
  toggle/hide path (gesture, REST, Ctrl+Alt+o) sends `dbus-send` commands
  to. `run.sh` now checks for that service (`NameHasOwner` via
  `dbus-send`) for up to 5s right after starting Onboard and logs clearly
  whether it registered - a direct, definitive answer instead of
  inferring from symptoms

## v1.4.12 - July 2026

- **Fix: found the actual root cause of GPU acceleration being fully
  disabled**, thanks to the v1.4.11 stderr capture - captured directly
  from a real device:
  ```
  ERROR:ui/gl/init/gl_factory.cc:110] Requested GL implementation
  (gl=egl-gles2,angle=none) not found in allowed implementations:
  [(gl=egl-angle,angle=default)].
  ERROR:components/viz/service/main/viz_main_impl.cc:190] Exiting GPU
  process due to errors during initialization
  ```
  `--use-gl=egl` requests the old direct-EGL path *without* going through
  ANGLE. Modern Chromium on Linux ("ANGLE everywhere") no longer supports
  that combination at all - the GPU process was exiting immediately on
  every single launch, silently leaving every GPU feature (compositing,
  rasterization, WebGL, ...) disabled/software for the rest of that run.
  It never crashed the browser or failed CDP, so nothing built so far
  could catch it - only reading Chromium's own stderr (v1.4.11) surfaced
  it. Switched to `--use-gl=angle` with no explicit `--use-angle=`
  override, which is exactly what the error message itself says is
  accepted (`angle=default`)

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
