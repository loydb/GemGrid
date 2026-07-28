"""
anim_grid.py - tile a folder of animated GIFs into one animated WebP grid.

Built for GemCutStudio turntable exports, but makes no assumptions about them:
sources may differ in frame count, per-frame delay and pixel size.  Everything
is resampled onto one fixed-rate output timeline, shorter clips loop inside the
window rather than freezing on their last frame, and each frame is contain-fit
(aspect preserved, centered, never cropped or stretched) into its cell.

The grid shape is chosen automatically from the file count, and the cell size is
auto-solved to the largest resolution that still fits the output size budget.

Output is animated WebP: roughly a third the size of the equivalent GIF, which
buys ~1.75x the cell resolution for the same budget.  It is also full colour
(no 256-entry palette, no dithering) and stores frame delays in exact
milliseconds rather than GIF's 10ms centiseconds.

    python anim_grid.py <input_folder> <output.webp> [options]

    python anim_grid.py "D:\\gems" grid.webp
    python anim_grid.py "D:\\gems" grid.webp --max-mb 80 --fps 25
    python anim_grid.py "D:\\gems" grid.webp --cols 5 --rows 6 --cell 360
    python anim_grid.py "D:\\gems" grid.webp --lossless

Sources are opened read-only and are never modified.

VIEWING: Windows Photos and Explorer preview render only the FIRST frame of an
animated WebP - the file is fine, the viewer is not.  Open it in Edge, Chrome
or Firefox to play it.

Embedding: set LOG to redirect progress output and CANCEL to a predicate that
returns True to abort; main() accepts an argv list.  gemgrid_gui.py drives it
that way.
"""

import argparse
import bisect
import io
import math
import os
import re
import sys

from PIL import Image, ImageSequence

# --- defaults (all overridable on the command line) --------------------------
DEF_MAX_MB      = 40.0    # output size budget, drives the auto resolution
DEF_FPS         = 20      # output timeline rate
DEF_GAP         = 6       # px between cells, and the outer margin
DEF_BG          = "#000000"
DEF_ASPECT      = 4 / 3   # preferred grid shape, w:h, when picking cols x rows
DEF_QUALITY     = 80      # WebP quality 0-100
DEF_METHOD      = 4       # WebP effort 0-6; 6 is slower and a little smaller
DEF_MAX_CELL    = 512     # ceiling for the auto-solved cell size
DEF_MIN_CELL    = 48      # floor; below this the budget is simply unreachable
DEF_MEM_GB      = 8.0     # cap on the decoded-frame cache
DEFAULT_DELAY_MS = 100    # fallback for a missing or 0 ms source frame delay

SIZE_MARGIN     = 0.95    # aim slightly under budget; encoders vary
PROBE_FRAMES    = 12      # sampled frames used to estimate bytes/frame
CELL_STEP       = 8       # auto-solved cell sizes snap to this multiple
WEBP_MAX_DIM    = 16383   # hard format limit on either axis

# --- embedding hooks (the GUI replaces these) --------------------------------
LOG = print
CANCEL = lambda: False


class Cancelled(Exception):
    """Raised when the CANCEL hook asks for an early exit."""


def _tick():
    if CANCEL():
        raise Cancelled()


def natural_key(s):
    """Sort key so gem2 precedes gem10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def parse_color(s):
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    parts = [int(p) for p in s.replace(",", " ").split()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("colour must be #rrggbb or 'r,g,b'")
    return tuple(parts)


def choose_layout(n, aspect):
    """Pick cols x rows: few empty cells, shape near the target aspect.

    Trades the two off rather than ranking them, so a prime count like 7 gets
    4x2 (one empty cell) instead of a 7x1 ribbon.
    """
    best, best_cost = None, None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        empty = cols * rows - n
        cost = empty + 3.0 * abs(math.log((cols / rows) / aspect))
        if best_cost is None or cost < best_cost:
            best, best_cost = (cols, rows), cost
    return best


def fit_contain(img, w, h, bg):
    """Aspect-preserved contain-fit onto a solid background, centered."""
    sw, sh = img.size
    if sw <= 0 or sh <= 0:
        return Image.new("RGB", (w, h), bg)
    scale = min(w / sw, h / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    holder = Image.new("RGBA", (w, h), bg + (255,))
    holder.alpha_composite(img.resize((nw, nh), Image.LANCZOS),
                           ((w - nw) // 2, (h - nh) // 2))
    return holder.convert("RGB")


def load_source(path, cell, bg):
    """Decode one GIF into contain-fit cells plus its per-frame timeline.

    Frames are read through ImageSequence.Iterator and converted to RGBA before
    fitting, so GIF frame disposal and partial-frame updates are composited by
    Pillow rather than left as torn deltas.  Returns (cells, ends_ms, total_ms)
    where ends_ms[i] is the exclusive end timestamp of frame i.
    """
    cells, ends, t = [], [], 0
    with Image.open(path) as im:
        for frame in ImageSequence.Iterator(im):
            delay = frame.info.get("duration") or 0
            if delay <= 0:
                delay = DEFAULT_DELAY_MS
            cells.append(fit_contain(frame.convert("RGBA"), cell, cell, bg))
            t += delay
            ends.append(t)
    if not cells:
        raise ValueError("no frames decoded from %s" % path)
    return cells, ends, t


def sample_index(src, t_ms):
    """Index of the source frame visible at t_ms, looping shorter clips."""
    t = t_ms % src["total"]
    i = bisect.bisect_right(src["ends"], t)
    return min(i, len(src["cells"]) - 1)


def compose(sources, order, cell, cols, gap, bg, size, frame_ids):
    """Render the requested output frames at a given cell size."""
    grid_w, grid_h = size
    out = []
    for k in frame_ids:
        _tick()
        canvas = Image.new("RGB", (grid_w, grid_h), bg)
        for idx, s in enumerate(sources):
            c_img = s["cells"][order[idx][k]]
            if c_img.size[0] != cell:
                c_img = c_img.resize((cell, cell), Image.LANCZOS)
            r, c = divmod(idx, cols)
            canvas.paste(c_img, (gap + c * (cell + gap), gap + r * (cell + gap)))
        out.append(canvas)
    return out


def encode_webp(frames, delay_ms, loop, fp, quality, method, lossless):
    frames[0].save(fp, format="WEBP", save_all=True, append_images=frames[1:],
                   duration=delay_ms, loop=loop, quality=quality,
                   method=method, lossless=lossless, minimize_size=True)


def grid_size(cols, rows, cell, gap):
    return (cols * cell + (cols + 1) * gap, rows * cell + (rows + 1) * gap)


def cell_ceiling(cols, rows, gap):
    """Largest cell size that keeps both axes inside the WebP limit."""
    by_w = (WEBP_MAX_DIM - (cols + 1) * gap) // cols
    by_h = (WEBP_MAX_DIM - (rows + 1) * gap) // rows
    return max(1, min(by_w, by_h))


def build_parser():
    ap = argparse.ArgumentParser(
        prog="anim_grid.py",
        description="Tile a folder of animated GIFs into one animated WebP grid.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("input_dir", help="folder of source GIFs")
    ap.add_argument("output", help="output filename (bare name lands in input_dir)")
    ap.add_argument("--max-mb", type=float, default=DEF_MAX_MB,
                    help="size budget; cell size is solved up to this")
    ap.add_argument("--fps", type=float, default=DEF_FPS)
    ap.add_argument("--cols", type=int, help="override auto layout")
    ap.add_argument("--rows", type=int, help="override auto layout")
    ap.add_argument("--aspect", type=float, default=DEF_ASPECT,
                    help="preferred grid w:h when auto-picking the layout")
    ap.add_argument("--cell", type=int, help="fixed cell px; skips auto-sizing")
    ap.add_argument("--max-cell", type=int, default=DEF_MAX_CELL)
    ap.add_argument("--min-cell", type=int, default=DEF_MIN_CELL)
    ap.add_argument("--gap", type=int, default=DEF_GAP)
    ap.add_argument("--bg", type=parse_color, default=parse_color(DEF_BG),
                    help="background/letterbox colour, #rrggbb or 'r,g,b'")
    ap.add_argument("--quality", type=int, default=DEF_QUALITY,
                    help="WebP quality 0-100")
    ap.add_argument("--method", type=int, default=DEF_METHOD,
                    help="WebP effort 0-6; 6 is slower and slightly smaller")
    ap.add_argument("--lossless", action="store_true",
                    help="lossless WebP; much bigger, ignores --quality")
    ap.add_argument("--loop", type=int, default=0, help="0 = forever")
    ap.add_argument("--mem-gb", type=float, default=DEF_MEM_GB,
                    help="cap on the decoded-frame cache")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the plan and exit without encoding")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)

    src_dir = os.path.abspath(a.input_dir)
    if not os.path.isdir(src_dir):
        sys.exit("not a folder: %s" % src_dir)
    out_path = a.output if os.path.isabs(a.output) or os.path.dirname(a.output) \
        else os.path.join(src_dir, a.output)
    out_path = os.path.abspath(out_path)
    if not out_path.lower().endswith(".webp"):
        out_path = os.path.splitext(out_path)[0] + ".webp"
        LOG("output is WebP; writing %s" % os.path.basename(out_path))

    files = sorted((f for f in os.listdir(src_dir) if f.lower().endswith(".gif")),
                   key=natural_key)
    if not files:
        sys.exit("no source GIFs in %s" % src_dir)

    n = len(files)
    cols, rows = (a.cols, a.rows) if a.cols and a.rows else choose_layout(n, a.aspect)
    if a.cols and not a.rows:
        cols, rows = a.cols, math.ceil(n / a.cols)
    if a.rows and not a.cols:
        rows, cols = a.rows, math.ceil(n / a.rows)
    if cols * rows < n:
        sys.exit("layout %dx%d holds %d cells, need %d" % (cols, rows, cols * rows, n))
    LOG("%d GIF(s) -> %dx%d grid (%d empty cell(s))"
        % (n, cols, rows, cols * rows - n))

    hard_cap = cell_ceiling(cols, rows, a.gap)
    if a.cell and a.cell > hard_cap:
        sys.exit("cell %d px exceeds the %d px WebP dimension limit (max %d here)"
                 % (a.cell, WEBP_MAX_DIM, hard_cap))

    # --- pass 1: count frames, so the cache can be sized --------------------
    total_frames = 0
    for f in files:
        with Image.open(os.path.join(src_dir, f)) as im:
            total_frames += getattr(im, "n_frames", 1)

    cache_cell = min(a.cell or a.max_cell, hard_cap)
    est_gb = total_frames * cache_cell * cache_cell * 3 / (1024. ** 3)
    if est_gb > a.mem_gb:
        cache_cell = max(a.min_cell, int(math.sqrt(
            a.mem_gb * (1024. ** 3) / (total_frames * 3))) // CELL_STEP * CELL_STEP)
        LOG("  frame cache capped: cell %d -> %d px (%.1f GB budget, %d frames)"
            % (min(a.cell or a.max_cell, hard_cap), cache_cell, a.mem_gb,
               total_frames))

    # --- pass 2: decode every source once, fitted to the cache cell size -----
    LOG("decoding %d source frames at %d px..." % (total_frames, cache_cell))
    sources = []
    for f in files:
        _tick()
        cells, ends, total = load_source(os.path.join(src_dir, f), cache_cell, a.bg)
        sources.append({"name": f, "cells": cells, "ends": ends, "total": total})
        LOG("  %-56s %4d frames  %6.2fs" % (f[:56], len(cells), total / 1000.))
    loaded_cell = cache_cell

    def ensure_cells(cell):
        """Re-fit from the originals when the final size differs from the cache.

        Probing reuses the cached cells and downscales them, which is fine for
        estimating bytes.  For the frames that actually ship, resampling twice
        (native -> cache -> final) is softer than going straight there, so the
        sources are re-read once at the solved size.
        """
        nonlocal loaded_cell
        if cell == loaded_cell:
            return
        LOG("re-decoding at %d px for a single-step resample..." % cell)
        for s, f in zip(sources, files):
            _tick()
            s["cells"] = load_source(os.path.join(src_dir, f), cell, a.bg)[0]
        loaded_cell = cell

    window_ms = max(s["total"] for s in sources)
    step_ms = 1000.0 / a.fps
    n_out = max(1, int(round(window_ms / step_ms)))
    delay_ms = max(1, int(round(step_ms)))      # WebP delays are exact ms
    LOG("window %.2fs -> %d frames @ %d ms (%.2f fps effective)"
        % (window_ms / 1000., n_out, delay_ms, 1000. / delay_ms))

    order = [[sample_index(s, k * step_ms) for k in range(n_out)] for s in sources]
    budget = a.max_mb * 1024 * 1024

    def est_bytes(cell):
        """Encode a sample of output frames and extrapolate the full file.

        Sampled frames are temporally distant, so they defeat WebP's
        inter-frame prediction more than real consecutive frames would - the
        estimate errs high, which is the safe direction against a budget.
        """
        ids = sorted(set(round(k * (n_out - 1) / max(1, PROBE_FRAMES - 1))
                         for k in range(min(PROBE_FRAMES, n_out))))
        fr = compose(sources, order, cell, cols, a.gap, a.bg,
                     grid_size(cols, rows, cell, a.gap), ids)
        buf = io.BytesIO()
        encode_webp(fr, delay_ms, a.loop, buf, a.quality, a.method, a.lossless)
        return buf.tell() / len(ids) * n_out

    # --- solve the largest cell size that fits the budget --------------------
    if a.cell:
        cell = a.cell
        LOG("cell fixed at %d px" % cell)
    else:
        lo = a.min_cell - a.min_cell % CELL_STEP
        hi = min(a.max_cell, cache_cell, hard_cap)
        hi -= hi % CELL_STEP
        top = est_bytes(hi)
        LOG("  probe cell %3d px -> ~%.1f MB" % (hi, top / 1048576))
        if top <= budget * SIZE_MARGIN:
            cell = hi
            LOG("  ceiling fits the budget; raise --max-cell for more")
        else:
            best = lo
            while lo <= hi:
                mid = max(a.min_cell, ((lo + hi) // 2) // CELL_STEP * CELL_STEP)
                e = est_bytes(mid)
                LOG("  probe cell %3d px -> ~%.1f MB" % (mid, e / 1048576))
                if e <= budget * SIZE_MARGIN:
                    best = mid
                    lo = mid + CELL_STEP
                else:
                    hi = mid - CELL_STEP
            cell = best
        LOG("solved cell size: %d px" % cell)

    gw, gh = grid_size(cols, rows, cell, a.gap)
    LOG("output %dx%d px, %d frames" % (gw, gh, n_out))
    if a.dry_run:
        return out_path

    # --- final render, with one corrective shrink if the estimate undershot --
    for attempt in range(3):
        gw, gh = grid_size(cols, rows, cell, a.gap)
        ensure_cells(cell)
        frames = compose(sources, order, cell, cols, a.gap, a.bg, (gw, gh),
                         range(n_out))
        LOG("encoding %d frames..." % n_out)
        encode_webp(frames, delay_ms, a.loop, out_path, a.quality, a.method,
                    a.lossless)
        mb = os.path.getsize(out_path) / 1048576.
        if a.cell or mb <= a.max_mb or cell <= a.min_cell or attempt == 2:
            break
        shrunk = max(a.min_cell,
                     int(cell * math.sqrt(budget * SIZE_MARGIN / (mb * 1048576)))
                     // CELL_STEP * CELL_STEP)
        if shrunk >= cell:
            break
        LOG("  %.2f MB over budget, retrying at cell %d px" % (mb, shrunk))
        cell = shrunk

    with Image.open(out_path) as chk:
        n_chk, anim = getattr(chk, "n_frames", 1), getattr(chk, "is_animated", False)

    LOG("\nwrote %s" % out_path)
    LOG("  %dx%d px, %d frames, %d ms/frame, %.2f s, %.2f MB"
        % (gw, gh, n_out, delay_ms, n_out * delay_ms / 1000., mb))
    LOG("  verified animated: %s, %d frames" % (anim, n_chk))
    if mb > a.max_mb:
        LOG("  NOTE: still over the %.0f MB budget at the %d px floor."
            % (a.max_mb, cell))
        LOG("  Levers: lower --quality, lower --fps, smaller --max-cell.")
    LOG("  Windows Photos and Explorer preview show only frame 1 of an"
        " animated WebP - open it in a browser to play it.")
    return out_path


if __name__ == "__main__":
    main()
