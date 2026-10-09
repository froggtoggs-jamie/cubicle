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
# Plank stores its settings through GSettings; the keyfile backend lets the
# image seed them (skel/.config/glib-2.0/settings/keyfile) without dconf.
export GSETTINGS_BACKEND=keyfile
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_CACHE_HOME" "$HOME/.config"
chmod 700 "$XDG_RUNTIME_DIR"

# The home directory is an empty tmpfs on every start; seed the desktop
# configuration (dock items, GTK theme, window manager) from the image.
cp -R /opt/cubicle-computer/skel/. "$HOME/"

# Chromium profile (also on tmpfs). Use the window manager's title bar rather
# than Chrome's own, so the "no minimize button" rule applies to the browser
# too; there is no task list to restore a minimized window from.
if [ ! -f /tmp/profile/Default/Preferences ]; then
    mkdir -p /tmp/profile/Default
    printf '{"browser":{"custom_chrome_frame":false,"check_default_browser":false}}' > /tmp/profile/Default/Preferences
fi

# X server with VNC built in. Loopback only; the driver bridges it over the
# token-protected port, so nothing unauthenticated reaches the network.
Xtigervnc "$DISPLAY" -geometry "${WIDTH}x${HEIGHT}" -depth 24 -rfbport 5900 -localhost \
    -SecurityTypes None -AlwaysShared -AcceptSetDesktopSize=0 -desktop "Cubicle" \
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

# Wallpaper: the generated default, or a file mounted over it by the host
# (COMPUTER_DOCKER_WALLPAPER). Fall back to a flat colour if it cannot load.
xsetroot -solid '#15171c'
feh --no-fehbg --bg-fill /opt/cubicle-computer/wallpaper >/tmp/feh.log 2>&1 || true

# The compositor gives Plank its transparent theme and smooth hiding.
xfwm4 --compositor=on >/tmp/xfwm4.log 2>&1 &
sleep 0.5
plank >/tmp/plank.log 2>&1 &

exec node /opt/cubicle-computer/driver.mjs
