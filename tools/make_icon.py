#!/usr/bin/env python3
"""Draw the grass-block app icon as PNGs, using only the standard library.

    python3 tools/make_icon.py

Writes static/icons/icon-{32,76,152,180}.png.
"""

import os
import random
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "icons")

GRASS = [(0x6a, 0xae, 0x45), (0x5d, 0x9c, 0x3c), (0x7b, 0xc0, 0x52)]
DIRT = [(0x86, 0x60, 0x43), (0x76, 0x53, 0x39), (0x95, 0x6e, 0x4e)]
OUTLINE = (0x2b, 0x21, 0x18)
GRID = 16


def build_grid():
    """A 16x16 grass block: green cap, jagged edge, dirt below."""
    rng = random.Random(20260922)
    pixels = []
    for y in range(GRID):
        row = []
        for x in range(GRID):
            if y < 4:
                row.append(rng.choice(GRASS))
            elif y == 4:
                # the ragged line where grass spills onto the dirt
                row.append(rng.choice(GRASS) if (x * 7 + 3) % 5 < 3 else rng.choice(DIRT))
            elif y == 5:
                row.append(rng.choice(GRASS) if (x * 3) % 7 == 0 else rng.choice(DIRT))
            else:
                row.append(rng.choice(DIRT))
        pixels.append(row)

    for i in range(GRID):
        pixels[0][i] = OUTLINE
        pixels[GRID - 1][i] = OUTLINE
        pixels[i][0] = OUTLINE
        pixels[i][GRID - 1] = OUTLINE
    return pixels


def write_png(path, size, grid):
    """Nearest-neighbour upscale, so the file is exactly the size iOS asked for."""
    width = height = size

    rows = []
    for y in range(height):
        source = grid[y * GRID // height]
        row = bytearray()
        for x in range(width):
            r, g, b = source[x * GRID // width]
            row += bytes((r, g, b))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    with open(path, "wb") as handle:
        handle.write(png)
    return width


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    grid = build_grid()
    for size in (32, 76, 152, 180):
        path = os.path.join(OUT_DIR, "icon-%d.png" % size)
        actual = write_png(path, size, grid)
        print("wrote %s (%dx%d)" % (path, actual, actual))


if __name__ == "__main__":
    main()
