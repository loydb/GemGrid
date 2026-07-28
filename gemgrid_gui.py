"""
gemgrid_gui.py - desktop front end for anim_grid.py.

Pick a folder of animated GIFs, name the output, tick Lossless if you want it,
press Build.  Everything else (grid shape, cell resolution) is solved
automatically.  The full option set stays available on the anim_grid.py command
line; this window is deliberately the three decisions that actually vary.

    python gemgrid_gui.py

Built to an .exe with build_exe.py.
"""

import datetime
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import anim_grid

APP_TITLE = "GemGrid"

# Lossless exists to preserve fidelity, so holding it to the lossy byte budget
# would be self-defeating - it would just shrink the cells until it fit.  The
# budget is relaxed instead, and the choice is logged rather than hidden.
LOSSY_BUDGET_MB = 40.0
LOSSLESS_BUDGET_MB = 400.0


def default_name():
    return "gemgrid-%s.webp" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=12)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(5, weight=1)

        self.q = queue.Queue()
        self.worker = None
        self.cancel_flag = threading.Event()
        self.result_path = None

        self.src = tk.StringVar()
        self.out = tk.StringVar(value=default_name())
        self.lossless = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Pick a folder of animated GIFs.")

        ttk.Label(self, text="Source folder").grid(row=0, column=0, sticky="w",
                                                   pady=(0, 4))
        ttk.Entry(self, textvariable=self.src).grid(row=0, column=1, sticky="ew",
                                                    padx=(8, 8), pady=(0, 4))
        self.browse_btn = ttk.Button(self, text="Browse...", command=self.pick)
        self.browse_btn.grid(row=0, column=2, sticky="ew", pady=(0, 4))

        ttk.Label(self, text="Output file").grid(row=1, column=0, sticky="w",
                                                 pady=(0, 4))
        ttk.Entry(self, textvariable=self.out).grid(row=1, column=1, columnspan=2,
                                                    sticky="ew", padx=(8, 0),
                                                    pady=(0, 4))

        self.loss_chk = ttk.Checkbutton(self, text="Lossless (slower)",
                                        variable=self.lossless)
        self.loss_chk.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(2, 8))

        bar = ttk.Frame(self)
        bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        bar.columnconfigure(3, weight=1)
        self.build_btn = ttk.Button(bar, text="Build", command=self.start)
        self.build_btn.grid(row=0, column=0)
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.cancel,
                                     state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=(6, 0))
        self.open_btn = ttk.Button(bar, text="Open in browser",
                                   command=self.open_result, state="disabled")
        self.open_btn.grid(row=0, column=2, padx=(6, 0))
        self.folder_btn = ttk.Button(bar, text="Show folder",
                                     command=self.open_folder, state="disabled")
        self.folder_btn.grid(row=0, column=3, padx=(6, 0), sticky="w")

        self.pbar = ttk.Progressbar(self, mode="indeterminate")
        self.pbar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        wrap = ttk.Frame(self)
        wrap.grid(row=5, column=0, columnspan=3, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.log = tk.Text(wrap, height=16, width=88, wrap="none",
                           state="disabled", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

        ttk.Label(self, textvariable=self.status, foreground="#444").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))

        master.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.pump)

    # --- helpers ---------------------------------------------------------
    def write(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick(self):
        d = filedialog.askdirectory(title="Folder of animated GIFs",
                                    initialdir=self.src.get() or None)
        if d:
            self.src.set(os.path.normpath(d))
            n = len([f for f in os.listdir(d) if f.lower().endswith(".gif")])
            self.status.set("%d GIF%s found." % (n, "" if n == 1 else "s"))

    def open_result(self):
        if self.result_path and os.path.exists(self.result_path):
            webbrowser.open("file:///" + self.result_path.replace("\\", "/"))

    def open_folder(self):
        if self.result_path and os.path.exists(self.result_path):
            os.startfile(os.path.dirname(self.result_path))

    # --- build -----------------------------------------------------------
    def start(self):
        src = self.src.get().strip()
        name = self.out.get().strip() or default_name()
        if not src or not os.path.isdir(src):
            messagebox.showerror(APP_TITLE, "Pick a source folder first.")
            return
        gifs = [f for f in os.listdir(src) if f.lower().endswith(".gif")]
        if not gifs:
            messagebox.showerror(APP_TITLE, "No .gif files in that folder.")
            return
        if not name.lower().endswith(".webp"):
            name = os.path.splitext(name)[0] + ".webp"
            self.out.set(name)
        dest = name if os.path.isabs(name) else os.path.join(src, name)
        if os.path.exists(dest) and not messagebox.askyesno(
                APP_TITLE, "%s already exists.\n\nOverwrite it?"
                % os.path.basename(dest)):
            return

        budget = LOSSLESS_BUDGET_MB if self.lossless.get() else LOSSY_BUDGET_MB
        argv = [src, name, "--max-mb", str(budget)]
        if self.lossless.get():
            argv.append("--lossless")

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.write("source : %s  (%d GIFs)" % (src, len(gifs)))
        self.write("output : %s" % dest)
        self.write("mode   : %s, size budget %.0f MB"
                   % ("lossless" if self.lossless.get() else "lossy q80", budget))
        self.write("-" * 76)

        self.result_path = None
        self.cancel_flag.clear()
        for b in (self.build_btn, self.browse_btn, self.open_btn, self.folder_btn):
            b.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.pbar.start(12)
        self.status.set("Building - this takes a few minutes for a large folder.")

        self.worker = threading.Thread(target=self.run_build, args=(argv,),
                                       daemon=True)
        self.worker.start()

    def run_build(self, argv):
        anim_grid.LOG = lambda m="": self.q.put(("log", str(m)))
        anim_grid.CANCEL = self.cancel_flag.is_set
        try:
            path = anim_grid.main(argv)
            self.q.put(("done", path))
        except anim_grid.Cancelled:
            self.q.put(("cancelled", None))
        except SystemExit as e:            # argparse / sys.exit inside main
            self.q.put(("error", str(e)))
        except Exception as e:
            self.q.put(("error", "%s: %s" % (type(e).__name__, e)))
        finally:
            anim_grid.LOG = print
            anim_grid.CANCEL = lambda: False

    def cancel(self):
        self.cancel_flag.set()
        self.status.set("Cancelling after the current step...")
        self.cancel_btn.configure(state="disabled")

    def finish(self, status):
        self.pbar.stop()
        for b in (self.build_btn, self.browse_btn):
            b.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.status.set(status)

    def pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.write(payload)
                elif kind == "done":
                    self.result_path = payload
                    mb = os.path.getsize(payload) / 1048576.
                    self.finish("Done - %s (%.1f MB)"
                                % (os.path.basename(payload), mb))
                    for b in (self.open_btn, self.folder_btn):
                        b.configure(state="normal")
                    self.out.set(default_name())   # don't clobber it next run
                elif kind == "cancelled":
                    self.write("\ncancelled.")
                    self.finish("Cancelled.")
                elif kind == "error":
                    self.write("\nERROR: %s" % payload)
                    self.finish("Failed.")
                    messagebox.showerror(APP_TITLE, payload)
        except queue.Empty:
            pass
        self.after(100, self.pump)

    def on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_TITLE, "A build is running. Quit anyway?"):
                return
            self.cancel_flag.set()
        self.master.destroy()


def selftest(report_path):
    """Headless smoke test, meant to be run against the frozen .exe.

    A PyInstaller build can look fine and still be missing Pillow's WebP
    encoder - a gap that would otherwise only surface after a user sat through
    a whole build.  This proves the bundled binary can decode GIFs and write an
    animated WebP.  Writes a report file because --windowed has no console.
    """
    import tempfile
    import traceback
    from PIL import Image

    lines = []
    ok = False
    try:
        d = tempfile.mkdtemp(prefix="gemgrid_selftest_")
        for i in range(3):
            fr = [Image.new("RGB", (64, 64), (40 * i + 10, 30 * j + 20, 128))
                  for j in range(4)]
            fr[0].save(os.path.join(d, "clip%d.gif" % i), save_all=True,
                       append_images=fr[1:], duration=80, loop=0)
        anim_grid.LOG = lambda m="": lines.append(str(m))
        path = anim_grid.main([d, "selftest.webp", "--cell", "48"])
        with Image.open(path) as im:
            ok = bool(getattr(im, "is_animated", False)) and im.n_frames > 1
            lines.append("frames=%d size=%s animated=%s"
                         % (im.n_frames, im.size, im.is_animated))
        lines.append("frozen=%s" % getattr(sys, "frozen", False))
    except Exception:
        lines.append(traceback.format_exc())
    lines.append("RESULT: %s" % ("PASS" if ok else "FAIL"))
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        report = sys.argv[i + 1] if len(sys.argv) > i + 1 else "gemgrid_selftest.txt"
        sys.exit(selftest(report))

    root = tk.Tk()
    root.title(APP_TITLE)
    root.minsize(720, 520)
    try:
        root.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
