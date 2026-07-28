"""
test_anim_grid.py - end-to-end checks for anim_grid.py.

The real GemCutStudio corpus is uniform (all 720x720, all 30ms), so it never
exercises the paths anim_grid.py claims to handle.  This builds a deliberately
hostile synthetic corpus and verifies the output pixel by pixel.

Each synthetic frame is a flat colour that encodes its own identity:
    G = source index * 30   -> proves the cell landed in the right grid slot
    R = 10 + frame index * 9 -> proves the right frame was sampled at that time
So reading one pixel out of the finished grid answers "which clip, which frame".
Expected values come from the delays used to author the sources, not from
re-reading them, so the timeline maths is checked against ground truth.

    python test_anim_grid.py [workdir]
"""

import os
import subprocess
import sys
import shutil
import tempfile

from PIL import Image, ImageSequence

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "anim_grid.py")

FALLBACK_MS = 100  # what gif_grid.py must substitute for a 0/missing delay

# name, (w, h), n_frames, delays -> int, list, or None for "write no duration"
CORPUS = [
    ("clip1 wide.gif",    (240, 120), 10, 40),
    ("clip2 tall.gif",    (100, 300),  4, 250),
    ("clip10 square.gif", (128, 128), 25, 30),          # natural-sort: after 2
    ("clip3 varied.gif",  (160, 160),  6, [50, 150, 50, 300, 100, 80]),
    ("clip4 zerodelay.gif", (128, 128), 5, 0),          # -> 100ms fallback
    ("clip5 nodelay.gif", (128, 128),  5, None),        # -> 100ms fallback
    ("clip6 static.gif",  (200, 200),  1, 500),
]
TRANSPARENT = "clip7 alpha.gif"  # separate: disposal=2 + transparency


def frame_color(src_i, frame_i):
    return (10 + frame_i * 9, (src_i * 30) % 256, 128)


def build_corpus(d):
    for si, (name, size, n, delays) in enumerate(CORPUS):
        frames = [Image.new("RGB", size, frame_color(si, i)).convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=8) for i in range(n)]
        kw = {}
        if delays is not None:
            kw["duration"] = delays
        frames[0].save(os.path.join(d, name), save_all=True,
                       append_images=frames[1:], loop=0, **kw)

    # transparency + disposal=2: a centred block on a transparent field.  The
    # cell centre must show the block colour, the cell corner must show the
    # grid background - i.e. transparency composited, not smeared.
    n, size, si = 8, (150, 150), len(CORPUS)
    frames = []
    for i in range(n):
        im = Image.new("P", size, 0)
        pal = [255, 0, 255]  # idx0 - transparent, deliberately garish
        pal += list(frame_color(si, i))
        pal += [0] * (768 - len(pal))
        im.putpalette(pal)
        block = Image.new("P", (60, 60), 1)
        im.paste(block, (45, 45))
        frames.append(im)
    frames[0].save(os.path.join(d, TRANSPARENT), save_all=True,
                   append_images=frames[1:], loop=0, duration=60,
                   transparency=0, disposal=2)


def truth_timeline(delays, n):
    """(ends_ms, total_ms) from the delays used to author a clip."""
    if delays is None or delays == 0:
        per = [FALLBACK_MS] * n
    elif isinstance(delays, int):
        per = [delays] * n
    else:
        per = list(delays)
    ends, t = [], 0
    for d in per:
        t += d
        ends.append(t)
    return ends, t


def expected_index(ends, total, t_ms):
    t = t_ms % total
    for i, e in enumerate(ends):
        if t < e:
            return i
    return len(ends) - 1


def natural_key(s):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def run(args):
    r = subprocess.run([sys.executable, TARGET] + args, capture_output=True,
                       text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:]); print(r.stderr[-2000:])
    return r


def main():
    workdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        tempfile.gettempdir(), "gif_grid_test")
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)
    build_corpus(workdir)

    fails, checks = [], 0

    def check(cond, label):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append(label)
            print("  FAIL  %s" % label)

    print("corpus: %d clips in %s" % (len(CORPUS) + 1, workdir))

    # ---- main build: 8 clips, mismatched everything ------------------------
    fps, cell, gap = 20, 96, 6
    r = run([workdir, "grid.webp", "--cell", str(cell), "--gap", str(gap),
             "--fps", str(fps), "--lossless"])
    check(r.returncode == 0, "build exits 0")
    if r.returncode != 0:
        sys.exit("build failed, aborting")
    out = os.path.join(workdir, "grid.webp")
    check(os.path.exists(out), "grid.webp written")

    names = sorted([n for n, *_ in CORPUS] + [TRANSPARENT], key=natural_key)
    # 8 clips -> layout should be 4x2 at the default 4:3 aspect
    cols, rows = 4, 2
    check("-> %dx%d grid" % (cols, rows) in r.stdout, "auto layout is 4x2")

    im = Image.open(out)
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(im)]
    exp_w = cols * cell + (cols + 1) * gap
    exp_h = rows * cell + (rows + 1) * gap
    check(im.size == (exp_w, exp_h), "dims %s == %s" % (im.size, (exp_w, exp_h)))

    # window = longest clip.  clip3 varied = 730ms, clip6 static = 500ms,
    # clip2 tall = 1000ms, clip10 = 750ms, clip1 = 400ms, zero/no delay = 500ms
    truths = {}
    for si, (name, _, n, delays) in enumerate(CORPUS):
        truths[name] = (si,) + truth_timeline(delays, n)
    window = max(t[2] for t in truths.values())
    check(window == 1000, "window is the longest clip (%d ms)" % window)
    step = 1000.0 / fps
    check(len(frames) == round(window / step),
          "frame count %d == %d" % (len(frames), round(window / step)))
    check(im.info.get("duration") == 50, "delay 50ms exact (no centisecond rounding)")
    check(im.info.get("loop") == 0, "loops forever")

    # ---- the real assertion: right clip, right frame, right cell, right time
    bad = 0
    for k, fr in enumerate(frames):
        for slot, name in enumerate(names):
            if name == TRANSPARENT:
                continue
            si, ends, total = truths[name]
            want = expected_index(ends, total, k * step)
            wr, wg, _ = frame_color(si, want)
            r_, c_ = divmod(slot, cols)
            px = fr.getpixel((gap + c_ * (cell + gap) + cell // 2,
                              gap + r_ * (cell + gap) + cell // 2))
            if abs(px[0] - wr) > 4 or abs(px[1] - wg) > 4:
                bad += 1
                if bad <= 5:
                    print("    frame %d slot %d (%s): got %s want %s"
                          % (k, slot, name, px, (wr, wg, 128)))
    check(bad == 0, "every cell shows the correct source frame "
                    "(%d/%d samples wrong)" % (bad, len(frames) * (len(names) - 1)))

    # ---- looping: the 400ms clip must restart, not freeze ------------------
    si, ends, total = truths["clip1 wide.gif"]
    slot = names.index("clip1 wide.gif")
    r_, c_ = divmod(slot, cols)
    pt = (gap + c_ * (cell + gap) + cell // 2, gap + r_ * (cell + gap) + cell // 2)
    seen = [frames[k].getpixel(pt)[0] for k in range(len(frames))]
    check(len(set(seen)) > 1, "short clip animates")
    check(seen[0] == seen[int(round(total / step))],
          "short clip loops back to frame 0 at %d ms" % total)
    check(len(set(seen[-4:])) > 1, "short clip does not freeze at the end")

    # ---- transparency + disposal=2 ----------------------------------------
    slot = names.index(TRANSPARENT)
    r_, c_ = divmod(slot, cols)
    x0, y0 = gap + c_ * (cell + gap), gap + r_ * (cell + gap)
    centre = frames[0].getpixel((x0 + cell // 2, y0 + cell // 2))
    corner = frames[0].getpixel((x0 + 2, y0 + 2))
    check(abs(centre[1] - (len(CORPUS) * 30) % 256) <= 4,
          "transparent clip: block colour at centre %s" % (centre,))
    check(max(corner) <= 8, "transparent clip: field is background, not "
                            "magenta smear %s" % (corner,))

    # ---- flags -------------------------------------------------------------
    r = run([workdir, "grid.webp", "--cols", "2", "--cell", "64", "--dry-run"])
    check(r.returncode == 0 and "-> 2x4 grid" in r.stdout,
          "--cols alone infers rows (2x4)")
    check(not os.path.exists(os.path.join(workdir, "nope.gif")),
          "--dry-run writes nothing")

    r = run([workdir, "grid.webp", "--rows", "1", "--cell", "48", "--dry-run"])
    check(r.returncode == 0 and "-> 8x1 grid" in r.stdout, "--rows alone infers cols")

    r = run([workdir, "grid.webp", "--cols", "2", "--rows", "2", "--dry-run"])
    check(r.returncode != 0, "too-small layout is rejected")

    sub = os.path.join(workdir, "out")
    os.makedirs(sub, exist_ok=True)
    dest = os.path.join(sub, "named.webp")
    r = run([workdir, dest, "--cell", "48"])
    check(os.path.exists(dest), "absolute output path honoured")

    r = run([workdir, "grid.webp", "--cell", "64", "--bg", "#204060",
             "--gap", "10", "--lossless"])
    g = Image.open(os.path.join(workdir, "grid.webp")).convert("RGB")
    check(max(abs(a - b) for a, b in zip(g.getpixel((2, 2)), (32, 64, 96))) <= 6,
          "--bg colours the gutter %s" % (g.getpixel((2, 2)),))

    # budget solver: a tiny budget must drive the cell size down, not crash
    r = run([workdir, "grid.webp", "--max-mb", "0.30"])
    ok = os.path.getsize(os.path.join(workdir, "grid.webp")) / 1048576.
    check(r.returncode == 0, "budget solve exits 0")
    check(ok <= 0.35, "budget respected (%.2f MB <= 0.30 target)" % ok)
    w = Image.open(os.path.join(workdir, "grid.webp"))
    check(getattr(w, "is_animated", False) and w.n_frames == len(frames),
          "output is animated (%d frames)" % getattr(w, "n_frames", 1))

    # a .gif output name must be redirected to .webp, not silently written as GIF
    r = run([workdir, "renamed.gif", "--cell", "48"])
    check(os.path.exists(os.path.join(workdir, "renamed.webp")),
          "a .gif output name is redirected to .webp")
    check(not os.path.exists(os.path.join(workdir, "renamed.gif")),
          "no GIF written for a .gif output name")

    # WebP dimension ceiling must be enforced, not hit at encode time
    r = run([workdir, "huge.webp", "--cols", "8", "--cell", "9000", "--dry-run"])
    check(r.returncode != 0 and "dimension limit" in (r.stdout + r.stderr),
          "oversize cell rejected against the 16383px WebP limit")

    # sources must be untouched
    check(all(os.path.getsize(os.path.join(workdir, n)) > 0 for n in names),
          "sources intact")
    check(len([f for f in os.listdir(workdir) if f.lower().endswith(".gif")])
          == len(names), "sources are the only GIFs in the folder")

    print("\n%d checks, %d failed" % (checks, len(fails)))
    for f in fails:
        print("  - %s" % f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
