"""Generate the default desktop wallpaper at image build time.

A quiet dark-blue gradient with a soft diagonal band, written as a PNG with
only the standard library so the image needs no extra packages. Users can
replace it per deployment with COMPUTER_DOCKER_WALLPAPER (see README).
"""

import struct
import sys
import zlib

WIDTH, HEIGHT = 1280, 720
TOP = (16, 22, 38)        # deep navy
BOTTOM = (28, 46, 84)     # slate blue
BAND = (64, 120, 200)     # soft accent


def _lerp(a, b, t):
    return int(a + (b - a) * t)


def _row(y):
    t = y / (HEIGHT - 1)
    base = tuple(_lerp(TOP[i], BOTTOM[i], t) for i in range(3))
    pixels = bytearray()
    for x in range(WIDTH):
        # Diagonal band centred on a line from bottom-left to top-right.
        d = abs((x / WIDTH) + (y / HEIGHT) - 1.15)
        glow = max(0.0, 1.0 - d / 0.35) ** 2 * 0.22
        pixels.extend(min(255, _lerp(base[i], BAND[i], glow)) for i in range(3))
    return bytes(pixels)


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def main(path):
    raw = b"".join(b"\x00" + _row(y) for y in range(HEIGHT))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(png)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "wallpaper")
