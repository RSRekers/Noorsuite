"""App icon for launchers: .ico generation and a Windows shortcut that uses it (all inside tmp dirs)."""
import subprocess
import sys

import pytest

from NoorSuite import shortcut


def test_make_icon_writes_square_ico(tmp_path):
    p = shortcut.make_icon(tmp_path / "x.ico", size=64)
    data = p.read_bytes()
    assert data[:4] == b"\x00\x00\x01\x00" and len(data) > 1000                 # ICO header


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")
def test_create_shortcuts_points_to_gui_python_with_icon(tmp_path, monkeypatch):
    monkeypatch.setattr(shortcut, "locations", lambda: {"desktop": tmp_path / "d", "start": tmp_path / "s"})
    monkeypatch.setattr(shortcut, "icon_path", lambda: tmp_path / "i.ico")
    made = shortcut.create_shortcuts()
    assert [m.name for m in made] == ["NoorSuite.lnk"] * 2 and all(m.exists() for m in made)
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{made[0]}');"
                          "$s.IconLocation;$s.Arguments"], capture_output=True, text=True).stdout
    assert "i.ico" in out and "-m NoorSuite" in out
