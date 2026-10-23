# Kiosk-chromium (v1.0.0)

<div align="center">

A modern, high-performance Chromium kiosk add-on for **Home Assistant OS**.

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-41BDF5?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Chromium](https://img.shields.io/badge/Chromium-Kiosk-4285F4?style=for-the-badge&logo=googlechrome&logoColor=white)](https://www.chromium.org/)
[![Docker](https://img.shields.io/badge/Docker-Multi--Arch-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Release](https://img.shields.io/badge/Release-v1.0.0-success?style=for-the-badge)](https://github.com/rhythmcreative/Kiosk-chromium/releases)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue?style=for-the-badge)](LICENSE)

</div>

---

## 📖 Overview

**Kiosk-chromium** provides a dedicated, full-screen Chromium display for Home Assistant OS. It runs an embedded Xorg display server, Openbox window manager, and a tuned Chromium browser directly on your local hardware (Raspberry Pi 4/5, Intel NUC, x86_64 mini PCs, and other SBCs).

Unlike legacy solutions based on Luakit or minimal webviews, **Kiosk-chromium** uses regular Chromium driven via the **Chrome DevTools Protocol (CDP)**. This provides full support for modern web standards, hardware-accelerated WebGPU/WebGL, custom Lovelace cards, media streaming, and seamless [Voice Satellite](https://github.com/jxlarrea/voice-satellite-card-integration) audio integration.

---

## 🚀 Key Features

* **⚡ Modern Chromium + CDP Engine**: Fully drives Chromium via CDP with native support for modern JavaScript, WebSockets, and complex custom Lovelace cards.
* **🎮 Hardware Acceleration (WebGPU & WebGL)**: Hardware-accelerated graphics for smooth animations, high-framerate graphs, and Voice Satellite integration.
* **🔑 Automatic Login**: Handles Home Assistant authentication automatically upon startup with session persistence.
* **🎙️ Voice Satellite Auto-Start**: Pre-seeds and auto-binds satellite entities in localStorage so your assist microphone and speaker work instantly after reboots.
* **👆 Multi-Touch Gestures & Onscreen Keyboard**: Native touchscreen support with customizable swipe/tap gestures and Onboard virtual keyboard.
* **🌐 Built-in REST API**: Remotely turn the display on/off, change URLs, trigger reloads, and check health over HTTP.
* **🌙 Dynamic Theme & UI Control**: Force dark/light themes, adjust UI zoom levels, hide the Home Assistant sidebar, or auto-hide top navigation headers.
* **🔄 Crash Recovery & Screen Blanking**: Automatic watchdogs recover from network glitches, reload hung pages, and support DPMS power saving with instant wake on touch.

---

## 📦 Installation

### One-Click Install (Recommended):

Click the button below to add this repository directly to your Home Assistant instance:

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frhythmcreative%2FKiosk-chromium)

### Manual Setup:
1. In Home Assistant, navigate to **Settings → Add-ons → Add-on Store**.
2. Click the three dots (**⋮**) in the top right corner and select **Repositories**.
3. Add the repository URL:
   ```text
   https://github.com/rhythmcreative/Kiosk-chromium
   ```
4. Find **Kiosk Chromium Display** in the store and click **Install**.
5. Go to the **Configuration** tab, enter your Home Assistant credentials, and click **Start**.

> [!IMPORTANT]
> A display must be physically plugged into your device before booting. Make sure you set your `ha_username` and `ha_password` in the add-on configuration tab.

---

## ⚙️ Configuration Options

| Option | Default | Description |
|---|---|---|
| `ha_username` | — | **Required.** Your Home Assistant username for automatic login. |
| `ha_password` | — | **Required.** Your Home Assistant password. |
| `ha_url` | `http://localhost:8123` | URL of the local Home Assistant instance. |
| `ha_dashboard` | `""` | Target dashboard path (leave blank for default Lovelace). |
| `zoom_level` | `100` | Display zoom percentage (e.g. `125` for high-DPI screens). |
| `browser_refresh` | `600` | Periodic reload in seconds (`0` disables). Keeps long-running dashboards fresh. |
| `screen_timeout` | `0` | Seconds of inactivity before screen blanks (`0` disables). |
| `pause_on_screen_off` | `true` | Freezes background rendering when the screen is blanked to save CPU/GPU power. |
| `voice_satellite_entity` | `""` | Entity ID, friendly name, or `auto` for Voice Satellite card integration. |
| `screen_orientation` | `0` | Screen rotation: `0` (normal), `90`, `180`, or `270` degrees. |
| `show_sidebar` | `false` | Whether to display the Home Assistant navigation sidebar. |

---

## 🎮 Controls & Shortcuts

* **Reload Page**: `Ctrl + R`
* **Navigation Menu**: Right-click or long-press on touchscreens to access Back, Forward, Stop, and Reload.
* **Capture Screenshot**: `Ctrl + Alt + K` (or quadruple 3-finger tap) saves a screenshot directly to `/media/screenshots`.
* **Toggle Virtual Keyboard**: `Ctrl + Alt + O` toggles the Onboard virtual keyboard.

---

## 🌐 REST API Endpoints

The add-on exposes an internal REST API on port `8080`:

| Endpoint | Method | Description |
|---|---|---|
| `/health` | `GET` | Health check endpoint returning status `200 OK`. |
| `/display` | `GET` / `POST` | Check or control display power (`{"power": "on"}` or `{"power": "off"}`). |
| `/url` | `POST` | Load a new URL on the active display (`{"url": "https://..."}`). |
| `/refresh` | `POST` | Trigger an immediate browser page reload. |

---

## 🛠️ Architecture & Hardware Support

* **Architectures**: `aarch64` (Raspberry Pi 4/5, ODROID, ARM64) and `amd64` (x86_64 mini PCs, NUCs).
* **Display Stack**: Direct DRM/KMS hardware acceleration via Xorg + Openbox + Chromium.
* **Input**: Native Linux evdev event processing (`/dev/input/event*`) supporting touchscreens, trackpads, and USB keyboards.

---

## 📄 License & Credits

* Licensed under **GPL-3.0**.
* Maintained by [rhythmcreative](https://github.com/rhythmcreative).
* Based on foundational work from [HAOS-kiosk](https://github.com/puterboy/HAOS-kiosk) by Jeff Kosowsky.
