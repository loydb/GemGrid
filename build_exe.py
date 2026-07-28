"""
build_exe.py - freeze the GemGrid GUI into a single-file Windows .exe.

    python build_exe.py            # build
    python build_exe.py --clean    # build after wiping build/ and dist/

Produces dist/GemGrid.exe.  Needs PyInstaller:  pip install pyinstaller
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "GemGrid"
ENTRY = os.path.join(HERE, "gemgrid_gui.py")


def main():
    if "--clean" in sys.argv:
        for d in ("build", "dist"):
            p = os.path.join(HERE, d)
            if os.path.isdir(p):
                shutil.rmtree(p)
                print("removed %s" % p)
        spec = os.path.join(HERE, NAME + ".spec")
        if os.path.exists(spec):
            os.remove(spec)

    # --onefile unpacks itself into %TEMP%\_MEIxxxx on every launch.  That is
    # tidier to hand out, but it fails where policy makes %TEMP% non-executable
    # and it pays the extraction cost at each start.  --onedir sidesteps both.
    onedir = "--onedir" in sys.argv
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir" if onedir else "--onefile",
        "--windowed",              # no console window behind the GUI
        "--name", NAME,
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", HERE,
        # anim_grid is imported by the GUI, but name it so a future refactor
        # that only touches it dynamically still gets it bundled
        "--hidden-import", "anim_grid",
        "--paths", HERE,
        # trim the obvious dead weight PyInstaller otherwise hoovers up
        "--exclude-module", "numpy",
        "--exclude-module", "matplotlib",
        "--exclude-module", "scipy",
        "--exclude-module", "pytest",
        ENTRY,
    ]
    icon = os.path.join(HERE, "gemgrid.ico")
    if os.path.exists(icon):
        cmd[cmd.index("--name"):cmd.index("--name")] = ["--icon", icon]

    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        sys.exit("PyInstaller failed (%d)" % r.returncode)

    exe = os.path.join(HERE, "dist", NAME, NAME + ".exe") if onedir \
        else os.path.join(HERE, "dist", NAME + ".exe")
    print("\nbuilt %s (%.1f MB)" % (exe, os.path.getsize(exe) / 1048576.))
    print("self-test it with:  \"%s\" --selftest report.txt" % exe)


if __name__ == "__main__":
    main()
