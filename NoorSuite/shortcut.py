"""Desktop / Start-menu shortcut with the NoorSuite icon (Windows).

pip's ``noorsuite.exe`` is a generic launcher stub and always shows Python's icon; a shortcut that starts
``pythonw -m NoorSuite`` shows the app icon in Explorer, the Start menu and (pinned) the taskbar, and opens
no console window.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ICON_SOURCE = Path(__file__).with_name("NOORSUITE_ICON.jpg")


def icon_path() -> Path:
    return Path.home() / ".noorsuite" / "noorsuite.ico"


def make_icon(dest=None, size: int = 256) -> Path:
    """Write the app icon as a square ``.ico`` (centre-cropped, scaled to ``size``)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage
    dest = Path(dest) if dest else icon_path()
    img = QImage(str(ICON_SOURCE))
    if img.isNull():
        raise OSError(f"cannot read {ICON_SOURCE}")
    side = min(img.width(), img.height())
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    img = img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(dest), "ICO"):
        raise OSError(f"cannot write {dest}")
    return dest


def locations() -> dict:
    appdata = os.environ.get("APPDATA", "")
    return {"desktop": Path.home() / "Desktop",
            "start": Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"}


def gui_python() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def create_shortcuts(where=("desktop", "start"), name: str = "NoorSuite") -> list[Path]:
    """Create ``NoorSuite.lnk`` in the given places and return the files."""
    if sys.platform != "win32":
        raise OSError("shortcuts are only supported on Windows (use your desktop's launcher editor)")
    icon = make_icon()
    made = []
    for key in where:
        folder = locations()[key]
        folder.mkdir(parents=True, exist_ok=True)
        lnk = folder / (name + ".lnk")
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:NS_LNK);"
              "$s.TargetPath=$env:NS_TARGET;$s.Arguments='-m NoorSuite';$s.IconLocation=$env:NS_ICON;"
              "$s.WorkingDirectory=$env:USERPROFILE;$s.Description='NoorSuite';$s.Save()")
        env = dict(os.environ, NS_LNK=str(lnk), NS_TARGET=gui_python(), NS_ICON=str(icon))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True, text=True)
        if r.returncode != 0 or not lnk.exists():
            raise OSError(f"could not create {lnk}: {r.stderr.strip()}")
        made.append(lnk)
    return made


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="noorsuite shortcut", description=__doc__)
    ap.add_argument("--where", choices=["desktop", "start", "both"], default="both")
    ap.add_argument("--icon-only", action="store_true", help="only write the .ico and print its path")
    a = ap.parse_args(argv)
    try:
        if a.icon_only:
            print(make_icon())
            return 0
        for p in create_shortcuts(("desktop", "start") if a.where == "both" else (a.where,)):
            print(f"shortcut: {p}")
    except OSError as exc:
        print(f"shortcut failed: {exc}")
        return 1
    return 0
