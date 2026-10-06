#!/bin/sh
# Boot the sandbox desktop, then hand over to the driver.
set -e

WIDTH="${VIEWPORT_WIDTH:-1280}"
HEIGHT="${VIEWPORT_HEIGHT:-720}"
export DISPLAY="${DISPLAY:-:1}"
export HOME="${HOME:-/home/pwuser}"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_CACHE_HOME="/tmp/cache"
export XDG_RUNTIME_DIR="/tmp/runtime-$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_CACHE_HOME" "$HOME/.config"
chmod 700 "$XDG_RUNTIME_DIR"

# The home directory is an empty tmpfs on every start; seed the desktop
# configuration (panel layout, dock launchers) from the image.
cp -R /opt/open-grok-computer/skel/. "$HOME/"

# X server with VNC built in. Loopback only; the driver bridges it over the
# token-protected port, so nothing unauthenticated reaches the network.
Xtigervnc "$DISPLAY" -geometry "${WIDTH}x${HEIGHT}" -depth 24 -rfbport 5900 -localhost \
    -SecurityTypes None -AlwaysShared -AcceptSetDesktopSize=0 -desktop "Open Grok Bot" \
    >/tmp/xvnc.log 2>&1 &

i=0
until xdpyinfo >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 100 ]; then
        echo "X server did not start" >&2
        cat /tmp/xvnc.log >&2 || true
        exit 1
    fi
    sleep 0.1
done

eval "$(dbus-launch --sh-syntax)"
export DBUS_SESSION_BUS_ADDRESS

xsetroot -solid '#15171c'
xfwm4 --compositor=off >/tmp/xfwm4.log 2>&1 &
xfce4-panel --disable-wm-check >/tmp/panel.log 2>&1 &

exec node /opt/open-grok-computer/driver.mjs
