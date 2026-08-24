# Kiosk-chromium

Display HA dashboards in kiosk mode on your HAOS server, using a regular
Chromium browser.

**Maintainer:** rhythmcreative · **Version:** 1.4.25 (August 2026) · Fork of
[HAOS-kiosk](https://github.com/puterboy/HAOS-kiosk) by Jeff Kosowsky,
driving Chromium via CDP instead of Luakit. See the
[CHANGELOG](CHANGELOG.md).

Launches Xorg + Openbox + Chromium (kiosk mode) on your configured Home
Assistant dashboard. Mouse, touchscreen, keyboard, and audio work out of the
box. Includes touch gestures, screen rotation, an onscreen keyboard, and a
REST API to control the display and push new URLs.

**Shortcuts:** `Ctrl+R` reloads the page. Right-click (or long-press on
touch) opens Back/Forward/Stop/Reload. `Ctrl+Alt+K` or the *quadruple
3-finger tap* gesture saves a screenshot to `/media/screenshots`.

> **Before you start:** HA username/password are required in
> **Configuration**, and a display must be connected — if it doesn't show
> up, reboot with it attached. Supports mouse/touch/keyboard devices whose
> `/dev/input/eventN` is below 25.
>
> Issues → check the [issues page](https://github.com/rhythmcreative/Kiosk-chromium/issues)
> (open + closed) first — most common problems are already answered there.
> If you still need to file one, include your hardware/display setup and a
> full log.

[![Buy Me a Coffee](https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png)](https://www.buymeacoffee.com/puterboy)

---

## Install

1. [![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frhythmcreative%2FKiosk-chromium)
   — or manually: **Add-on Store → ⋮ → Repositories** → add
   `https://github.com/rhythmcreative/Kiosk-chromium`
2. Install **Kiosk Chromium Display**.
3. Set your HA username/password under **Configuration**.
4. **Start**.

Trouble installing, or with displays/touchscreens? See
[Troubleshooting](#troubleshooting) below.

---

## Configuration options

| Option | Default | What it does |
|---|---|---|
| `ha_username` / `ha_password` | — | **Required.** Your HA login. |
| `ha_url` | `http://localhost:8123` | Rarely needs changing — runs on the local server. |
| `ha_dashboard` | `""` | Starting dashboard name; blank = default Lovelace. |
| `login_delay` | `1` sec | Delay before the login page is assumed loaded. |
| `zoom_level` | `100` | Percent zoom. |
| `browser_refresh` | `600` sec | Periodic reload; `0` disables. Recommended — console errors can otherwise overwrite the dashboard on default RPi configs. |
| `screen_timeout` | `0` | Seconds before the screen blanks; `0` = never. |
| `pause_on_screen_off` | `true` | Freezes the browser page while the screen is off (via `screen_timeout` or `display_off`), so it stops using CPU/GPU for a page nobody can see. Reloads automatically the moment the screen comes back on — expect a brief (~1-2s) reload flash on wake, traded for near-zero rendering work during the (often much longer) time the screen is off. |
| `output_number` | `1` | Which *connected* video output to use. Leave at `1` unless you have multiple outputs. |
| `dark_mode` | `true` | Prefers dark mode. Only takes effect on HA pages if the user profile's Theme is `auto`; profile `light`/`dark` and `ha_theme` (below) take precedence. |
| `ha_theme` | `""` | Force a specific HA theme. `{"dark":true}` / `{"dark":false}` force dark/light for HA dashboards; blank/`{}`/`"Home Assistant"` = auto, governed by `dark_mode`. |
| `ha_sidebar` | `None` | `Full` (icons+names) / `Narrow` (icons) / `None` (hidden). |
| `rotate_display` | `Normal` | `Normal` / `Left` / `Right` / `Inverted`. |
| `map_touch_inputs` | `true` | Rotates touch input to match `rotate_display`. |
| `cursor_timeout` | `5` sec | Hide cursor after inactivity; `0` = always show, `-1` = never show. |
| `browser_language` | `""` | BCP-47 locale (`es`, `es-ES`, `fr`, `pt-BR`, …). Sets Chromium's UI/spellcheck language, `navigator.language`, `Accept-Language`, and (unless the HA user profile sets its own language) the HA frontend language. Independent of `keyboard_layout` — that only affects which characters keys produce, not language. |
| `keyboard_layout` | `us` | xkb keyboard layout. |
| `onscreen_keyboard` | `true` | Auto-shows on focused text fields (via AT-SPI). Long-press `...` on the Return key to move/resize/configure; tap top-right corner or triple-click to toggle manually. See [Onboard](https://github.com/dr-ni/onboard). |
| `save_onscreen_config` | `true` | Persist onscreen-keyboard settings across sessions. |
| `xorg_conf` / `xorg_append_replace` | — | Append to or replace the default `xorg.conf`. Leave blank + `Append` to restore default. |
| `audio_sink` | `Auto` | `Auto` / `HDMI` / `USB` / `NONE`. |
| `voice_satellite` | `false` | Hands-free auto-start of [Voice Satellite](https://github.com/jxlarrea/voice-satellite-card-integration) (HACS): pre-grants mic access over CDP, treats a plain-http HA URL as a secure origin, and taps Voice Satellite's floating start button automatically after every boot/reload — no manual tapping. See [Voice Satellite](#voice-satellite). |
| `rest_ip` | `127.0.0.1` | REST server bind address. `0.0.0.0` accepts requests from anywhere — **only do this if you also set `rest_bearer_token`**, otherwise it's a real security hole. |
| `rest_port` | `8080` | REST port, 1024–49151. |
| `rest_bearer_token` | `""` | If set, REST calls need `-H "Authorization: Bearer <token>"`. |
| `gestures` | see below | Gesture-string → action-command map. See [Gesture Commands](#gesture-commands). |
| `command_whitelist` | see below | Regex of shell commands allowed in gestures / `run_command` / `run_commands`. Blank = allow everything not blacklisted; `.*` = allow *everything* (dangerous); `^$` = allow nothing. Bare names resolve against `/bin:/usr/bin:/usr/local/bin`. Built-in blacklist: `python`, `ash/bash/sh/su`, `env/exec`, `kill/killall/pkill`, `cp/chmod/chown/dd/ln/mv/rm/tar`, `mount/umount`, `curl/nc/wget`, `find/xargs`. |
| `vnc_server` | `""` | Password to enable VNC on port 5900 (`-` = no password). Unencrypted, network-wide — use with caution. |
| `debug_mode` | `false` | Starts Xorg/Openbox/REST but not Chromium, then idles. Launch it manually inside the container (`sudo docker exec -it addon_haoskiosk bash`, then run chromium with `--remote-debugging-port=9222` etc.). |

---

## REST API

All endpoints: `http://localhost:<rest_port>/<endpoint>`. Only localhost can
call anything unless `rest_bearer_token` is set — then any caller with the
token can, on whichever `rest_ip` you configured. `run_command(s)`/`xset`/
`disable_inputs`/`enable_inputs` are always localhost-only unless a token is set.

| Endpoint | Method | Body | Does |
|---|---|---|---|
| `launch_url` | POST | `{"url": "..."}` (optional) | Navigate the kiosk tab. No body = default dashboard. |
| `refresh_browser` | POST | — | Reload the page. |
| `kiosk_status` | GET | — | Full state: running?, hardware/software GL, current URL, load-failure count, `page_frozen` (see `pause_on_screen_off`), and `gpu_info` (Chromium's real GPU feature status — the only reliable way to tell if compositing/rasterization/WebGL are *actually* hardware-accelerated). Check this if the dashboard feels laggy. |
| `is_display_on` | GET | — | Boolean. |
| `display_on` | POST | `{"timeout": N}` (optional) | Turn screen on; `0` = never blank again. |
| `display_off` | POST | — | Turn screen off. |
| `xset` | POST | `{"args": "..."}` | Run `xset <args>` (e.g. `-q` for display info). |
| `run_command` | POST | `{"cmd": "...", "cmd_timeout": N}` | Run one command in the container, subject to the whitelist/blacklist. |
| `run_commands` | POST | `{"cmds": [...], "cmd_timeout": N}` | Same, multiple commands. |
| `screenshot` | POST | `{"filename", "quality", "delay"}` (all optional) | Saves to `/media/screenshots`. JPEG unless filename ends `.bmp/.png/.pnm/.tiff`. |
| `current_processes` | GET | — | Active/max concurrent subprocess count. |
| `disable_inputs` / `enable_inputs` | POST | — | Block/unblock keyboard+pointer input. |
| `mute_audio` / `unmute_audio` / `toggle_audio` | POST | `unmute_audio` takes `{"volume": 0-150}` | Audio sink control. |

```bash
curl -X POST http://localhost:8080/launch_url -H "Content-Type: application/json" -d '{"url": "https://homeassistant.local/my_dashboard"}'
curl -X GET  http://localhost:8080/kiosk_status
curl -X POST http://localhost:8080/screenshot
```

`run_command`/`run_commands` return `{"success", "result": {"success", "stdout", "stderr", "error"?}}`
(`run_commands` returns a `"results"` array). Pipe through `jq -r .result.stdout`
(or `jq -r '.results[]?.stdout'`).

<details>
<summary><strong>Using these from Home Assistant automations</strong> (<code>configuration.yaml</code> <code>rest_command:</code> block)</summary>

```yaml
rest_command:
  haoskiosk_launch_url:
    url: "http://localhost:8080/launch_url"
    method: POST
    content_type: "application/json"
    payload: '{"url": "{{ url }}"}'

  haoskiosk_refresh_browser:
    url: "http://localhost:8080/refresh_browser"
    method: POST
    content_type: "application/json"
    payload: "{}"

  haoskiosk_is_display_on:
    url: "http://localhost:8080/is_display_on"
    method: GET
    content_type: "application/json"

  haoskiosk_display_on:
    url: "http://localhost:8080/display_on"
    method: POST
    content_type: "application/json"
    payload: >-
      {{ {'timeout': timeout | int if timeout is defined and timeout is number and timeout >= 0 else none} | to_json }}

  haoskiosk_display_off:
    url: "http://localhost:8080/display_off"
    method: POST
    content_type: "application/json"
    payload: "{}"

  haoskiosk_xset:
    url: "http://localhost:8080/xset"
    method: POST
    content_type: "application/json"
    payload: '{"args": "{{ args }}"}'

  haoskiosk_run_command:
    url: "http://localhost:8080/run_command"
    method: POST
    content_type: "application/json"
    payload: >-
      {{ {'cmd': cmd, 'cmd_timeout': cmd_timeout | int if cmd_timeout is defined and cmd_timeout is number and cmd_timeout > 0 else none} | to_json }}

  haoskiosk_run_commands:
    url: "http://localhost:8080/run_commands"
    method: POST
    content_type: "application/json"
    payload: >-
      {{ {'cmds': cmds, 'cmd_timeout': cmd_timeout | int if cmd_timeout is defined and cmd_timeout is number and cmd_timeout > 0 else none} | to_json }}

  haoskiosk_screenshot:
    url: "http://localhost:8080/screenshot"
    method: POST
    content_type: "application/json"
    payload: >-
      {{ {'delay': delay | int if delay is defined and delay | int(0) >= 0 else none,
          'filename': filename if filename is defined and filename != "" and "/" not in filename and "\0" not in filename else none,
          'quality': quality | int if quality is defined and 1 <= quality | int <= 100 else none} | to_json }}

  haoskiosk_current_processes:
    url: "http://localhost:8080/current_processes"
    method: GET
    content_type: "application/json"

  haoskiosk_disable_inputs:
    url: "http://localhost:8080/disable_inputs"
    method: POST
    content_type: "application/json"
    payload: "{}"

  haoskiosk_enable_inputs:
    url: "http://localhost:8080/enable_inputs"
    method: POST
    content_type: "application/json"
    payload: "{}"

  haoskiosk_mute_audio:
    url: "http://localhost:8080/mute_audio"
    method: POST
    content_type: "application/json"

  haoskiosk_unmute_audio:
    url: "http://localhost:8080/unmute_audio"
    method: POST
    content_type: "application/json"
    payload: >-
      {{ {'volume': volume | int if volume is defined and volume is number and 0 <= volume | int <= 150 else none} | to_json }}

  haoskiosk_toggle_audio:
    url: "http://localhost:8080/toggle_audio"
    method: POST
    content_type: "application/json"
```

If `rest_bearer_token` is set, add to every stanza above:

```yaml
    headers:
      Authorization: Bearer <REST_BEARER_TOKEN>
```

Reference from automations as `rest_command.haoskiosk_<name>`:

```yaml
actions:
  - action: rest_command.haoskiosk_launch_url
    data:
      url: "https://homeassistant.local/my_dashboard"
  - action: rest_command.haoskiosk_display_on
    data:
      timeout: 300
  - action: rest_command.haoskiosk_run_command
    data:
      cmd: "command"
      cmd_timeout: 5
```

</details>

**Use cases:** turn the display on/off by time/proximity/voice; rotate
through dashboards or cameras; build a screensaver from a loop over
`launch_url`. See the [`examples/`](examples) folder for a screensaver
script and an ultrasonic-distance trigger.

---

## Gesture Commands

A gesture command is `"<gesture-string>": <action>`. Both are strictly
validated at load — check the log if one fails to load.

**Gesture string:** `<CONTACTS>_<DEVICE>_<CLICKS>_<GESTURE>`

| Field | Format |
|---|---|
| `CONTACTS` | `N` / `N+` / `N-` (contact count), or for mouse a button list `[Left, Right]` / `[1,3]` |
| `DEVICE` | `MOUSE` / `TOUCH` / `ANY`, or the mechanism name `Button` / `Finger` |
| `CLICKS` | `M` / `M+` / `M-` clicks or taps |
| `GESTURE` | Class (`CLICKTAP`, `DRAG`, `SWIPE`, `LONG`, `CORNER_<name>`, `ANY`) or friendly name (`Click`, `Tap`, `Long Click`, …) |

Notes: matching is case-insensitive, and keys are matched in file order —
put specific gestures before wildcards. `DRAG`/`SWIPE` take `_LEFT/_RIGHT/_UP/_DOWN`
suffixes (undirected = wildcard for any direction). `LONG`/`DRAG`/`SWIPE`
(and variants) are always single-click. `CORNER_<name>` triggers within
`click_dim` (default 5px) of `TOPLEFT`/`TOPRIGHT`/`BOTLEFT`/`BOTRIGHT`.

```text
Valid:   [Left, Right]_MOUSE_3_CLICKTAP
         2_TOUCH_1_DRAG_LEFT
         2_Button_1_Long Click
         1+_ANY_1+_ANY
Invalid: 1_Mouse_3-Tap      (Tap is Touch-only)
         1-Touch_2-Long     (Long must be single-contact)
```

**Action command** — a string, a list, or a dict:

```jsonc
"ls -al"                                              // string; "" = no-op (blocks a wildcard)
["echo hello", ["ls", "-al"]]                         // list: strings and/or argv-lists
{"cmds": "ls -al", "msg": "listing", "timeout": 1}     // dict: cmds + optional msg/timeout
```

Commands are shell commands, or internal `kiosk.*` commands: `back`,
`forward`, `refresh_browser`, `launch_url [<url>]`, `display_on [<timeout>]`,
`display_off`, `toggle_keyboard`, `toggle_audio`. (`kiosk.launch_url` is the
*only* way to navigate — Chromium is driven over CDP, not spawned per URL.)

**Default bindings** (removable per-entry in the Configuration UI):

| Gesture | Action |
|---|---|
| Tap/click top-right corner, or left-triple-click, or 3-finger tap | Toggle onscreen keyboard |
| 3-finger double tap | Refresh browser |
| 3-finger triple tap | Toggle audio mute |
| 3-finger quadruple tap | Save screenshot |
| 3-finger swipe left / right | Forward / back in history |
| 2-finger triple tap | Restore default dashboard |
| 2-finger quadruple tap | Open Google search |

---

## Keyboard shortcuts

Standard Chromium shortcuts work on the page (`Ctrl+R` reload,
`Ctrl+Left`/`Ctrl+Right` history) — no address bar/tabs since it's
`--kiosk --app` mode. Openbox adds:

| Shortcut | Action |
|---|---|
| `Ctrl+Alt+O` | Toggle onscreen keyboard |
| `Ctrl+Alt+K` | Screenshot → `/media/screenshots` |
| `Ctrl+Alt+Shift+Left` / `Alt+Shift+Tab` | Previous window |
| `Ctrl+Alt+Shift+Right` / `Alt+Tab` | Next window |

---

## Voice Satellite

[Voice Satellite](https://github.com/jxlarrea/voice-satellite-card-integration)
(HACS) turns the kiosk into a real `assist_satellite`: wake word, conversations,
timers and announcements, all in the browser. Without help, though, it parks
behind a floating "tap to start" microphone button after every boot — browsers
refuse microphone capture without a user gesture, and this add-on wipes its
Chromium profile on each launch, so no remembered permission ever survives.

With the `voice_satellite` option enabled, the add-on starts it hands-free:

1. **Microphone pre-granted** — `Browser.grantPermissions` (`audioCapture`) is
   sent over CDP right after launch, before the first page finishes loading.
   No prompt, no gesture requirement. The fresh Chromium profile is also
   seeded with a mic-allow exception for your HA origin before launch, so
   there's never a prompt even on a fast first load — and the password
   manager is disabled entirely, so the "Save password?" bubble after
   auto-login is gone too.
2. **Plain-http instances work** — if `ha_url` is not https, Chromium is
   launched with `--unsafely-treat-insecure-origin-as-secure` for exactly that
   origin so `getUserMedia` exists at all (the browser-side equivalent of
   [Kiosk Satellite](https://github.com/jxlarrea/kiosk-satellite)'s secure-
   context proxy).
3. **Auto-tap fallback** — after every page load (boot, periodic refresh,
   websocket-recovery reload), the add-on watches for Voice Satellite's
   floating start button and taps it via CDP input events — *trusted* browser
   input, i.e. a real user activation, which plain JS `.click()` can't provide.

One-time setup: install Voice Satellite via HACS, add the integration once per
browser, open the **Voice Satellite** sidebar panel on the kiosk and pick this
device's satellite entity. After that, voice comes up by itself on every boot.

> **Screen-off tradeoff:** with `pause_on_screen_off` or a non-zero
> `screen_timeout`, the page freezes when the screen blanks and the wake-word
> engine stops with it. For always-listening voice keep the screen always on
> (`screen_timeout: 0`, `pause_on_screen_off: false`); the add-on logs a
> warning when the two are combined. The vsWakeWord engine additionally needs
> WebGPU (hardware GL) — check `/kiosk_status`'s `gpu_info` if in doubt.

---

## Troubleshooting

- **RPi3 display issues:** add to `config.txt`'s `[pi3]` section:
  `dtoverlay=vc4-fkms-v3d` and `max_framebuffers=2`.
- **Black borders (overscan) on RPi:** add `disable_overscan=1` to
  `config.txt`.
