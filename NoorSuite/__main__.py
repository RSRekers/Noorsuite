"""Entry point: ``python -m NoorSuite --gui [port]`` launches the NOORSUITE GUI."""
from __future__ import annotations

import sys

from .ipc import DEFAULT_PORT


def _set_windows_app_id(app_id: str) -> None:
    """Make Windows group the process under our own taskbar icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    port = DEFAULT_PORT
    if "--gui" in argv:
        i = argv.index("--gui")
        if i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass

    from PyQt6.QtWidgets import QApplication

    from .app import APP_ID, APP_NAME, SciSuiteWindow, app_icon

    _set_windows_app_id(APP_ID)
    app = QApplication(argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())

    window = SciSuiteWindow(port=port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
