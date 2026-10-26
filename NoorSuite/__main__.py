"""Entry point: ``python -m NoorSuite --gui [port]`` launches the NOORSUITE GUI."""
from __future__ import annotations

import sys

from .ipc import DEFAULT_PORT


USAGE = """usage: noorsuite [PROJECT | FOLDER] [--document [TEMPLATE]] [--notebook] [--gui [PORT]]
       noorsuite api|insert|latex|presentation|skill ...   (headless; 'api notebook' writes a notebook)

  PROJECT      a .sciproj file, or a folder (its project, or a new <folder>.sciproj in it)
  --document   start with a LaTeX document when the project has none (TEMPLATE: article, acs, beamer ...)
  --notebook   also write a starter Jupyter notebook next to the project (and open it if Jupyter is installed)
  --gui PORT   listen on another port than 55555
  noorsuite shortcut   Desktop + Start-menu shortcut with the app icon (Windows; no console window)
Jupyter is optional: the window, 'noorsuite api' and 'noorsuite insert' work without it."""


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
    if len(argv) > 1 and argv[1] in ("skill", "insert", "api", "latex", "presentation", "shortcut"):   # subcommands
        if argv[1] == "shortcut":
            from .shortcut import main as shortcut_main
            return shortcut_main(argv[2:])
        if argv[1] == "presentation":
            from .pptximport import main as pres_main
            return pres_main(argv[2:])
        if argv[1] == "latex":
            from .latex_build import main as latex_main
            return latex_main(argv[2:])
        if argv[1] == "skill":
            from .skill import main as skill_main
            return skill_main(argv[2:])
        if argv[1] == "api":
            from .api_cli import main as api_main
            return api_main(argv[2:])
        from .insert_cli import main as insert_main
        return insert_main(argv[2:])
    if any(a in ("-h", "--help") for a in argv[1:]):
        print(USAGE)
        return 0
    port, target, document, notebook = DEFAULT_PORT, None, None, False
    rest, i = argv[1:], 0
    while i < len(rest):
        a = rest[i]
        if a == "--gui":
            if i + 1 < len(rest) and rest[i + 1].isdigit():
                port = int(rest[i + 1])
                i += 1
        elif a == "--notebook":
            notebook = True
        elif a == "--document":
            document = "article"
            if i + 1 < len(rest) and not rest[i + 1].startswith("-") and target is not None:
                document = rest[i + 1]
                i += 1
        elif not a.startswith("-") and target is None:
            target = a
        i += 1
    if document:
        from . import templates
        if document not in dict(templates.list_templates()):
            print(f"unknown template {document!r}; choose from: " + ", ".join(
                t for t, _ in templates.list_templates()))
            return 2

    from PyQt6.QtWidgets import QApplication

    from .app import APP_ID, APP_NAME, SciSuiteWindow, app_icon, install_crash_guards

    _set_windows_app_id(APP_ID)
    app = QApplication(argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())

    install_crash_guards()
    window = SciSuiteWindow(port=port)
    window.show()
    if target:
        from .notebook import project_for
        window.open_project_path(project_for(target), create=True)
    if document and not window.documents:
        window.create_new_document(template=document, open_tab=True)
    if notebook:
        window.new_notebook()
    window.start_warmup()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
