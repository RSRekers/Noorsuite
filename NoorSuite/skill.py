"""Install / update the NoorSuite Claude skill and keep the package itself current.

``python -m NoorSuite skill install``   copy the bundled SKILL.md to ``~/.claude/skills/noorsuite/``
``python -m NoorSuite skill update``    pull the newest commit from GitHub, then re-install the skill
``python -m NoorSuite skill ensure``    like update, but at most once per day and never fatal
                                        (the skill itself runs this at the start of every use)
``python -m NoorSuite skill status``    versions, paths, whether the GUI is reachable

Stdlib only. Editable installs (``pip install -e``) are never pip-upgraded -- only the skill
file is refreshed -- so a development checkout is not overwritten.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_URL = "https://github.com/RSRekers/Noorsuite.git"
SKILL_NAME = "noorsuite"
CHECK_INTERVAL_S = 24 * 3600
_SOURCE = Path(__file__).with_name("skill") / "SKILL.md"


def skill_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return Path(base) / "skills" / SKILL_NAME


def _state_path() -> Path:
    return Path(os.path.expanduser("~")) / ".noorsuite" / "state.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _state_path().parent.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def _direct_url() -> dict:
    """pip's ``direct_url.json`` for this install (VCS commit, or editable dir), if any."""
    try:
        from importlib.metadata import distribution
        raw = distribution("NoorSuite").read_text("direct_url.json")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def installed_commit() -> str:
    return (_direct_url().get("vcs_info") or {}).get("commit_id", "")


def is_editable() -> bool:
    return bool((_direct_url().get("dir_info") or {}).get("editable"))


def remote_commit(timeout: int = 15) -> str:
    """Head commit of the GitHub repo's default branch, via ``git ls-remote`` (uses the
    user's git credentials, so it also works for a private repo). '' on any failure."""
    try:
        out = subprocess.run(["git", "ls-remote", REPO_URL, "HEAD"], capture_output=True,
                             text=True, timeout=timeout, check=True).stdout
        return out.split()[0] if out.strip() else ""
    except Exception:
        return ""


def render_skill() -> str:
    """The bundled SKILL.md with this interpreter's path substituted in (there may be no
    ``python`` on PATH, and the package lives in one specific environment)."""
    text = _SOURCE.read_text(encoding="utf-8")
    return text.replace("{{PYTHON}}", sys.executable.replace("\\", "/"))


def install_skill() -> Path:
    target = skill_dir() / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_skill(), encoding="utf-8")
    state = _load_state()
    state["skill_installed_with"] = "editable" if is_editable() else installed_commit()
    _save_state(state)
    return target


def update(force: bool = False, quiet: bool = False) -> bool:
    """Upgrade the package from GitHub if a newer commit exists, then refresh the skill.
    Returns True if the package was upgraded. Never raises on network/pip failure."""
    def say(msg):
        if not quiet:
            print(msg)

    upgraded = False
    if is_editable():
        say("[update] editable install -- skipping pip upgrade, refreshing skill only")
    else:
        remote, local = remote_commit(), installed_commit()
        if not remote:
            say("[update] could not reach GitHub -- keeping the installed version")
        elif remote != local or force:
            say(f"[update] {local[:8] or 'unknown'} -> {remote[:8]}: upgrading from GitHub ...")
            res = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade",
                                  "--quiet", f"git+{REPO_URL}"],
                                 capture_output=True, text=True)
            if res.returncode == 0:
                upgraded = True
            else:
                say("[update] pip failed:\n" + (res.stderr or res.stdout).strip()[-800:])
        else:
            say(f"[update] already at {local[:8]}")
    state = _load_state()
    state["last_check"] = time.time()
    _save_state(state)
    if upgraded:
        # this process still has the old code loaded -- let the new package write the skill
        subprocess.run([sys.executable, "-m", "NoorSuite", "skill", "install"])
    else:
        say(f"[skill] {install_skill()}")
    return upgraded


def ensure(quiet: bool = True) -> None:
    """Throttled :func:`update` (once per ``CHECK_INTERVAL_S``); also installs the skill file
    if it is missing. Safe to call on every use."""
    try:
        if time.time() - _load_state().get("last_check", 0) < CHECK_INTERVAL_S \
                and (skill_dir() / "SKILL.md").exists():
            return
        update(quiet=quiet)
    except Exception:
        pass


def status() -> str:
    from . import __version__
    from .protocol import DEFAULT_PORT, IPCClient
    ver = re.sub(r"\s+", " ", __version__)
    lines = [
        f"NoorSuite {ver}  ({'editable' if is_editable() else installed_commit()[:8] or 'not from git'})",
        f"python : {sys.executable}",
        f"skill  : {skill_dir() / 'SKILL.md'}  ({'installed' if (skill_dir() / 'SKILL.md').exists() else 'MISSING'})",
        f"GUI    : {'running' if IPCClient(DEFAULT_PORT).is_alive() else 'not running'} on port {DEFAULT_PORT}",
    ]
    last = _load_state().get("last_check")
    if last:
        lines.append(f"checked: {time.strftime('%Y-%m-%d %H:%M', time.localtime(last))}")
    return "\n".join(lines)


def main(args: list[str]) -> int:
    cmd = args[0] if args else "status"
    if cmd == "install":
        print(f"[skill] installed -> {install_skill()}")
    elif cmd == "update":
        update(force="--force" in args)
    elif cmd == "ensure":
        ensure()
    elif cmd == "status":
        print(status())
    else:
        print(__doc__)
        return 2
    return 0
