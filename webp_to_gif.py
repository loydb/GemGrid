"""
webp_to_gif.py - convert an animated WebP back to an animated GIF.

GemGrid outputs WebP because it is far smaller and full colour, but some
places still only take GIF - Facebook, older forums, email clients.  This
converts without going back to the sources.

    python webp_to_gif.py <input.webp> [output.gif] [options]

    python webp_to_gif.py docs/example.webp
    python webp_to_gif.py docs/example.webp --max-width 640
    python webp_to_gif.py docs/example.webp --max-mb 8 --no-dither

Output defaults to the input name with a .gif extension, beside the input.

Expect it to get much bigger: GIF is limited to 256 colours per frame and has
no inter-frame prediction worth the name, so a 1-2 MB WebP routinely becomes
10 MB or more.  --max-mb solves the scale down to a budget.
"""

import argparse
import io
import math
import os
import struct
import sys

from PIL import Image, ImageSequence

DEF_FALLBACK_MS = 100
CELL_STEP = 8
SIZE_MARGIN = 0.97


def anmf_durations(path):
    """Per-frame durations straight out of the WebP container.

    Pillow reports frame duration as None for some animated WebPs - notably
    ones where identical adjacent frames were coalesced - so the container's
    own ANMF chunks are the reliable source.
    """
    try:
        b = open(path, "rb").read()
    except OSError:
        return []
    out, i = [], 12
    while i + 8 <= len(b):
        tag = b[i:i + 4]
        size = struct.unpack("<I", b[i + 4:i + 8])[0]
        if tag == b"ANMF":
            out.append(struct.unpack("<I", b[i + 20:i + 23] + b"\x00")[0])
        i += 8 + size + (size & 1)
    return out


def load(path):
    """Frames as RGB, plus the per-frame delay in ms."""
    frames, durs = [], []
    with Image.open(path) as im:
        container = anmf_durations(path)
        for k, fr in enumerate(ImageSequence.Iterator(im)):
            frames.append(fr.convert("RGB"))
            d = fr.info.get("duration") or 0
            if d <= 0:
                d = container[k] if k < len(container) else 0
            durs.append(d if d > 0 else DEF_FALLBACK_MS)
    if not frames:
        sys.exit("no frames in %s" % path)
    return frames, durs


def quantize(frames, dither):
    """One shared 256-colour palette: smaller file, no inter-frame shimmer."""
    d = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    picks = frames[::max(1, len(frames) // 8)][:8]
    w, h = frames[0].size
    strip = Image.new("RGB", (w, h * len(picks)))
    for i, f in enumerate(picks):
        strip.paste(f, (0, i * h))
    ref = strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    return [f.quantize(palette=ref, dither=d) for f in frames]


def encode(frames, durs, fp, loop, dither):
    pal = quantize(frames, dither)
    # GIF stores centiseconds; keep every frame's own delay rather than
    # flattening to one rate, so variable-rate sources survive the round trip
    cs = [max(10, int(round(d / 10.0)) * 10) for d in durs]
    pal[0].save(fp, format="GIF", save_all=True, append_images=pal[1:],
                duration=cs, loop=loop, optimize=True, disposal=1)


def scaled(frames, width):
    if not width or width >= frames[0].size[0]:
        return frames
    w0, h0 = frames[0].size
    h = max(1, int(round(h0 * width / w0)))
    return [f.resize((width, h), Image.LANCZOS) for f in frames]


def main():
    ap = argparse.ArgumentParser(
        description="Convert an animated WebP to an animated GIF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--max-width", type=int, help="scale down to this width")
    ap.add_argument("--max-mb", type=float,
                    help="solve the width down to fit this budget")
    ap.add_argument("--no-dither", action="store_true",
                    help="smaller file, some banding on smooth gradients")
    ap.add_argument("--loop", type=int, default=0, help="0 = forever")
    a = ap.parse_args()

    src = os.path.abspath(a.input)
    if not os.path.isfile(src):
        sys.exit("no such file: %s" % src)
    out = os.path.abspath(a.output) if a.output \
        else os.path.splitext(src)[0] + ".gif"

    frames, durs = load(src)
    w, h = frames[0].size
    total = sum(durs) / 1000.
    print("%s\n  %dx%d, %d frames, %.2f s, %.2f MB"
          % (os.path.basename(src), w, h, len(frames), total,
             os.path.getsize(src) / 1048576.))

    dither = not a.no_dither
    work = scaled(frames, a.max_width)

    if a.max_mb:
        budget = a.max_mb * 1048576
        lo, hi, best = 64, work[0].size[0], None
        hi -= hi % CELL_STEP
        while lo <= hi:
            mid = max(64, ((lo + hi) // 2) // CELL_STEP * CELL_STEP)
            buf = io.BytesIO()
            encode(scaled(work, mid), durs, buf, a.loop, dither)
            n = buf.tell()
            print("  probe %4d px wide -> %.2f MB" % (mid, n / 1048576.))
            if n <= budget * SIZE_MARGIN:
                best = mid
                lo = mid + CELL_STEP
            else:
                hi = mid - CELL_STEP
        if best is None:
            sys.exit("cannot reach %.1f MB even at 64 px wide" % a.max_mb)
        work = scaled(work, best)

    encode(work, durs, out, a.loop, dither)
    mb = os.path.getsize(out) / 1048576.
    with Image.open(out) as chk:
        n_chk = getattr(chk, "n_frames", 1)
    print("\nwrote %s" % out)
    print("  %dx%d, %d frames, %.2f s, %.2f MB (%.1fx the WebP)"
          % (work[0].size[0], work[0].size[1], n_chk, total, mb,
             mb / (os.path.getsize(src) / 1048576.)))


if __name__ == "__main__":
    main()
