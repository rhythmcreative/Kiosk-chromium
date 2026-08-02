################################################################################
# Add-on: HAOS Kiosk Display (haoskiosk)
# File: Dockerfile
# Version: 1.4.24
# Copyright Jeff Kosowsky
# Date: August 2026
################################################################################

# Pinned to a specific Alpine line (3.24) rather than ':latest' - both for reproducible builds
# (two builds of this Dockerfile on different days should produce materially the same image) and
# to stay in lockstep with the Chromium package repository pin below, which targets the same
# Alpine version. ghcr.io/home-assistant/base publishes per-Alpine-version tags (e.g. '3.24',
# '3.24-<release>') alongside a floating 'latest' that moves independently as new Alpine versions
# are added - relying on 'latest' here would eventually re-diverge the two.
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.24
FROM $BUILD_FROM

#===============================================================================
##### Install X, Browser and all necessary dependencies
RUN apk update && apk add --no-cache \
## Xserver and graphics
     xorg-server \
     xf86-video-modesetting \
     mesa-dri-gallium \
     mesa-egl \
     mesa-gles \
     libdrm \
### X utils
     xdotool \
     xinput \
     xrandr \
     xset \
#     xev \
     libxkbcommon \
     setxkbmap \
### Input, udev
     xf86-input-libinput \
     libinput \
#     libinput-tools \
     udev \
     libinput-udev \
     libevdev \
     evtest \
### Window, mouse, keyboard management
     openbox \
     onboard \
     unclutter-xfixes \
     ttf-dejavu \
### D-Bus (session bus + dbus-send; previously pulled in transitively by luakit,
### needed explicitly now for Onboard's toggle/hide IPC and to avoid DBUS timeouts)
     dbus \
### Linux utils
     bash \
     util-linux \
     scrot \
### Full Timezone info
     icu-data-full \
### Python libraries
#    py3-pip \
    py3-aiohttp \
    py3-xlib \
### Sound
     pulseaudio-utils \
#     alsa-utils alsa-plugins-pulse \
### Video
#     gstreamer-tools \
#     gst-libav \
### VNC (for debugging) - run: x11vnc -display :0 -forever & \
      x11vnc \
# Browser (regular Chromium, driven externally via the Chrome DevTools Protocol -
# see chromium_kiosk.py - rather than Luakit's in-process Lua scripting)
# NOTE: keep this repository pin's Alpine version in lockstep with BUILD_FROM above - it's what
# determines which Chromium version actually ships (v3.21/community was Chromium 136.x; v3.24
# tracks 150.x, matching this base image's own Alpine version rather than an older archive).
   && apk add --no-cache --repository=https://dl-cdn.alpinelinux.org/alpine/v3.24/community \
        chromium \
        chromium-swiftshader \
        chromium-lang \
   && rm -rf /var/cache/apk/*

#===============================================================================

# Placed after the (expensive, rarely-changing) package install above rather than before it:
# BUILD_VERSION changes with every add-on release, and Docker layer caching invalidates every
# subsequent layer once one layer's content changes - having this before 'RUN apk add' forced a
# full Xorg/Chromium re-download+reinstall on every single release, even one that only touched
# run.sh or a Python file.
ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION}

##### Set the display variable
ENV DISPLAY=:0

##### Copy over 'xorg.conf.default'
COPY xorg.conf.default /etc/X11/
COPY translations/*.yaml /translations/

COPY run.sh /
RUN chmod a+x /run.sh

COPY mouse_touch_inputs.py gesture_commands.json /
COPY rest_server.py cdp_client.py chromium_kiosk.py /

CMD ["/run.sh"]
