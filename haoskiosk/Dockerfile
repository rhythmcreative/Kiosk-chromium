################################################################################
# Add-on: HAOS Kiosk Display (haoskiosk)
# File: Dockerfile
# Version: 1.4.13
# Copyright Jeff Kosowsky
# Date: July 2026
################################################################################

ARG BUILD_FROM=ghcr.io/home-assistant/base:latest
FROM $BUILD_FROM

ARG BUILD_VERSION
ENV ADDON_VERSION=${BUILD_VERSION}

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
   && apk add --no-cache --repository=https://dl-cdn.alpinelinux.org/alpine/v3.21/community \
        chromium \
        chromium-swiftshader \
   && rm -rf /var/cache/apk/*

#===============================================================================

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
