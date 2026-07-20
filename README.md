# Kiosk-chromium

Display Home Assistant dashboards in kiosk mode on your HAOS server — using a
regular, stock **Chromium** browser instead of a niche WebKit-based one.

Fork of [HAOS-kiosk](https://github.com/puterboy/HAOS-kiosk) by Jeff
Kosowsky, adapted to drive Chromium via the Chrome DevTools Protocol (CDP)
instead of Luakit. See [haoskiosk/CHANGELOG.md](haoskiosk/CHANGELOG.md) for
the full list of changes.

## Why this fork?

- **Regular Chromium** — the same rendering engine as a normal desktop
  browser, not a smaller/less-maintained WebKit browser. Better
  compatibility with the modern HA frontend, custom cards, and
  JavaScript-heavy dashboards.
- **Automatic GPU fallback** — tries hardware-accelerated rendering (EGL)
  first, falls back to software rendering (SwiftShader) if that fails to
  start, so it comes up reliably across different boards and GPUs.
- Everything else you'd expect — auto-login, forced dark/light mode,
  sidebar/theme forcing, periodic refresh, crash recovery, touch gestures,
  the REST API — works the same as the original add-on.

## Install

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add: `https://github.com/rhythmcreative/Kiosk-chromium`
3. Install **Kiosk Chromium Display**, set your HA username/password in its
   **Configuration** tab, then **Start**.

[![Open your Home Assistant instance and show the add Add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frhythmcreative%2FKiosk-chromium)

## Documentation

Full documentation — configuration options, REST API, gesture commands,
keyboard shortcuts, and troubleshooting — lives in the add-on's own README,
which is also what Home Assistant shows you inside the Add-on Store:

**[haoskiosk/README.md](haoskiosk/README.md)**

## Issues

Please file issues in this repo's
[issues page](https://github.com/rhythmcreative/Kiosk-chromium/issues).
Since this is a fork with a different browser under the hood, don't assume
an upstream HAOS-kiosk issue applies here (or vice versa) without checking.

## License

GPLv2, see [LICENSE](LICENSE). Originally authored by Jeff Kosowsky —
consider [buying him a coffee](https://www.buymeacoffee.com/puterboy) if
you find the underlying add-on useful.
