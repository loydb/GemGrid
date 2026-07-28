"""
gen_icon.py - draw gemgrid.ico for the GemGrid executable.

A round-brilliant crown viewed face-on: octagonal girdle, octagonal table, and
the kite/star facets between them.  Generated rather than checked in as an
opaque binary, so it can be retuned.

    python gen_icon.py

build_exe.py picks up gemgrid.ico automatically if it is present.
"""

import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "gemgrid.ico")
SIZES = [16, 24, 32, 48, 64, 128, 256]

R = 512                      # supersampled working size
GIRDLE = 0.46 * R            # outer radius
TABLE = 0.20 * R             # table radius

# cool sapphire-ish ramp; index by facet so adjacent faces differ
CROWN = [(38, 116, 190), (26, 92, 160), (58, 148, 214), (30, 104, 176),
         (46, 130, 200), (22, 84, 148), (52, 140, 208), (34, 110, 184)]
TABLE_FILL = (150, 214, 250)
EDGE = (14, 46, 80)


def poly(cx, cy, r, n, rot=0.0):
    return [(cx + r * math.cos(rot + 2 * math.pi * i / n),
             cy + r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]


def main():
    img = Image.new("RGBA", (R, R), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = R / 2
    rot = math.pi / 8                      # flat edge up

    girdle = poly(cx, cy, GIRDLE, 8, rot)
    table = poly(cx, cy, TABLE, 8, rot)

    # crown facets: each girdle edge lifts to the matching table edge
    for i in range(8):
        quad = [girdle[i], girdle[(i + 1) % 8], table[(i + 1) % 8], table[i]]
        d.polygon(quad, fill=CROWN[i % len(CROWN)], outline=EDGE)

    d.polygon(table, fill=TABLE_FILL, outline=EDGE)
    d.polygon(girdle, outline=EDGE)

    # a single specular wedge across the table, the way a real crown catches light
    hi = [table[5], table[6], (cx, cy)]
    d.polygon(hi, fill=(210, 240, 255))

    frames = [img.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(OUT, format="ICO",
                    sizes=[(s, s) for s in SIZES])
    print("wrote %s (%d sizes, %.1f KB)"
          % (OUT, len(SIZES), os.path.getsize(OUT) / 1024.))


if __name__ == "__main__":
    main()
