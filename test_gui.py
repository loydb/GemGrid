"""
test_gui.py - integration checks for gemgrid_gui.py.

The engine has its own suite; this covers what only the GUI owns: the worker
thread, the queue pump that carries log lines back to the widget, the button
state machine, overwrite prompting, and cancellation.  It drives the real
widgets - no mocked App - and pumps Tk's event loop by hand instead of calling
mainloop(), so it runs unattended.

Skips cleanly where Tk has no display (headless Linux CI without xvfb).

    python test_gui.py
"""

import os
import shutil
import sys
import tempfile
import time

try:
    import tkinter as tk
except ImportError:
    print("tkinter unavailable - skipped")
    sys.exit(0)

from PIL import Image

import gemgrid_gui


def build_corpus(d, clips=4, frames=6, size=(64, 64)):
    for i in range(clips):
        fr = [Image.new("RGB", size, (30 * j + 20, 40 * i + 30, 128))
              for j in range(frames)]
        fr[0].save(os.path.join(d, "clip%d.gif" % i), save_all=True,
                   append_images=fr[1:], duration=80, loop=0)


def pump(root, app, until, timeout=180):
    """Spin Tk's event loop until a predicate holds or time runs out."""
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        root.update_idletasks()
        if until():
            return True
        time.sleep(0.02)
    return False


def main():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print("no display (%s) - skipped" % e)
        return 0
    root.withdraw()

    fails, checks = [], 0

    def check(cond, label):
        nonlocal checks
        checks += 1
        if not cond:
            fails.append(label)
            print("  FAIL  %s" % label)

    workdir = tempfile.mkdtemp(prefix="gemgrid_gui_test_")
    build_corpus(workdir)

    # the App must never block on a dialog during an unattended run
    answers = {"askyesno": True}
    gemgrid_gui.messagebox.askyesno = lambda *a, **k: answers["askyesno"]
    errors = []
    gemgrid_gui.messagebox.showerror = lambda title, msg, *a, **k: errors.append(msg)

    app = gemgrid_gui.App(root)

    # ---- refuses to run without a source ---------------------------------
    app.src.set("")
    app.start()
    check(bool(errors), "empty source folder is rejected")
    check(not app.build_btn.instate(["disabled"]), "Build stays enabled after reject")

    errors.clear()
    app.src.set(tempfile.mkdtemp(prefix="gemgrid_empty_"))
    app.start()
    check(bool(errors), "folder with no GIFs is rejected")

    # ---- a real build, driven exactly as a click would -------------------
    errors.clear()
    app.src.set(workdir)
    first_name = app.out.get()
    app.start()
    check(app.build_btn.instate(["disabled"]), "Build disabled while running")
    check(not app.cancel_btn.instate(["disabled"]), "Cancel enabled while running")

    done = pump(root, app, lambda: app.result_path is not None
                and not app.build_btn.instate(["disabled"]))
    check(done, "build completed inside the timeout")
    check(not errors, "build raised no error dialog (%s)" % (errors[:1],))

    out = app.result_path
    check(out and os.path.exists(out), "output file written")
    if out and os.path.exists(out):
        with Image.open(out) as im:
            check(getattr(im, "is_animated", False), "output is animated")
            check(im.n_frames > 1, "output has >1 frame (%d)" % im.n_frames)
        check(os.path.basename(out) == first_name,
              "output used the name in the box")

    check(not app.build_btn.instate(["disabled"]), "Build re-enabled after success")
    check(app.cancel_btn.instate(["disabled"]), "Cancel disabled after success")
    check(not app.open_btn.instate(["disabled"]), "Open in browser enabled")
    check(not app.folder_btn.instate(["disabled"]), "Show folder enabled")
    check(app.out.get() != first_name,
          "output name regenerated so the next run cannot clobber it")

    log_text = app.log.get("1.0", "end")
    check("wrote" in log_text, "engine output reached the log pane")
    check("stored frame" in log_text, "verification line reached the log pane")

    # ---- overwrite prompt is honoured ------------------------------------
    answers["askyesno"] = False
    app.out.set(os.path.basename(out))          # name that now exists
    before = os.path.getmtime(out)
    app.result_path = None
    app.start()
    check(app.result_path is None and not app.build_btn.instate(["disabled"]),
          "declining the overwrite prompt cancels the build")
    check(os.path.getmtime(out) == before, "declined overwrite left the file alone")
    answers["askyesno"] = True

    # ---- cancellation ----------------------------------------------------
    big = tempfile.mkdtemp(prefix="gemgrid_gui_big_")
    build_corpus(big, clips=9, frames=40, size=(320, 320))
    app.src.set(big)
    app.out.set("cancelme.webp")
    app.result_path = None
    app.start()
    pump(root, app, lambda: "decoding" in app.log.get("1.0", "end"), timeout=60)
    app.cancel()
    stopped = pump(root, app, lambda: not app.build_btn.instate(["disabled"]),
                   timeout=120)
    check(stopped, "cancel returned the UI to idle")
    check(app.result_path is None, "cancelled build produced no result path")
    check("cancelled" in app.log.get("1.0", "end").lower(),
          "cancellation was reported in the log")
    check(not os.path.exists(os.path.join(big, "cancelme.webp")),
          "cancelled build wrote no output file")

    root.destroy()
    for d in (workdir, big):
        shutil.rmtree(d, ignore_errors=True)

    print("\n%d checks, %d failed" % (checks, len(fails)))
    for f in fails:
        print("  - %s" % f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
