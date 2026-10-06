# Sandbox computer: a small XFCE desktop on an Xvnc display with Chromium,
# a file manager and a terminal, driven by driver.mjs and viewable over VNC.
FROM mcr.microsoft.com/playwright:v1.62.1-noble

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tigervnc-standalone-server \
        xfwm4 xfce4-panel thunar xfce4-terminal \
        dbus-x11 x11-utils x11-xserver-utils xdotool scrot feh \
        adwaita-icon-theme \
        python3-pip jq unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/open-grok-computer

COPY package.json ./
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm install --omit=dev --ignore-scripts

COPY driver.mjs start.sh chromium-browser make_wallpaper.py ./
COPY skel ./skel
# The dock's Chromium launcher reuses the browser Playwright started, so the
# wrapper and the launcher icon need the exact build Playwright resolved.
RUN CHROME="$(node -e "console.log(require('playwright').chromium.executablePath())")" \
    && sed -i "s#__CHROME__#${CHROME}#" chromium-browser \
    && sed -i "s#__CHROME_ICON__#$(dirname "${CHROME}")/product_logo_48.png#" skel/.config/xfce4/panel/launcher-1/chromium.desktop \
    && install -m 0755 chromium-browser /usr/local/bin/chromium-browser \
    && chmod 0755 start.sh \
    && python3 make_wallpaper.py wallpaper \
    && mkdir -p /workspace \
    && chown -R pwuser:pwuser /opt/open-grok-computer /workspace

USER pwuser
ENV PORT=3000 \
    WORKSPACE=/workspace \
    DISPLAY=:1 \
    HOME=/home/pwuser \
    VIEWPORT_WIDTH=1280 \
    VIEWPORT_HEIGHT=720
EXPOSE 3000

ENTRYPOINT ["/opt/open-grok-computer/start.sh"]
