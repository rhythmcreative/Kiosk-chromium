<h1 align="center">Kiosk-chromium </h1>

<div align="center">

<p><i> Chromium kiosk setup for Home Assistant. </i></p>

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-41BDF5?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Chromium](https://img.shields.io/badge/Chromium-4285F4?style=for-the-badge&logo=googlechrome&logoColor=white)](https://www.chromium.org/)
[![Linux](https://img.shields.io/badge/Linux-FCC624?style=for-the-badge&logo=linux&logoColor=black)](https://www.linux.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

</div>

Home Assistant dashboards in kiosk mode on your HAOS server — using stock
**Chromium** instead of a niche WebKit browser.

Fork of [HAOS-kiosk](https://github.com/puterboy/HAOS-kiosk) by Jeff
Kosowsky, driving Chromium via the Chrome DevTools Protocol (CDP) instead of
Luakit. Full history: [CHANGELOG](haoskiosk/CHANGELOG.md) ·
[Releases](https://github.com/rhythmcreative/Kiosk-chromium/releases).

## Why this fork

- **Real Chromium** — same engine as a desktop browser, so modern HA
  frontends, custom cards, and heavy JS dashboards just work.
- **Automatic GPU fallback** — tries hardware rendering first, drops to
  software if that fails, so it comes up reliably on any board.
- Everything else you'd expect: auto-login, dark/light mode, sidebar/theme
  forcing, periodic refresh, crash recovery, touch gestures, REST API.

## Install

1. HA → **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add `https://github.com/rhythmcreative/Kiosk-chromium`
3. Install **Kiosk Chromium Display**, set your HA username/password under
   **Configuration**, then **Start**.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frhythmcreative%2FKiosk-chromium)

## Documentation

Config options, REST API, gesture commands, and troubleshooting all live in
the add-on's own README (also what HA shows under the add-on's
**Documentation** tab):

**→ [haoskiosk/README.md](haoskiosk/README.md)**

## Issues & License

- Bugs/questions: [issues page](https://github.com/rhythmcreative/Kiosk-chromium/issues).
  This fork runs a different browser under the hood, so don't assume an
  upstream HAOS-kiosk issue applies here without checking.
- License: [GPLv2](LICENSE). Originally authored by Jeff Kosowsky — consider
  [buying him a coffee](https://www.buymeacoffee.com/puterboy).
