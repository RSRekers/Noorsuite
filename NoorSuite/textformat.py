"""Text formatting commands for the LaTeX editor (bold, colour, size, lists ... on a selection)."""

from __future__ import annotations

SIZES = ["tiny", "scriptsize", "footnotesize", "small", "normalsize", "large", "Large", "LARGE",
         "huge", "Huge"]

_SIMPLE = {
    "bold": ("\\textbf{", "}"), "italic": ("\\textit{", "}"), "emph": ("\\emph{", "}"),
    "underline": ("\\underline{", "}"), "mono": ("\\texttt{", "}"),
    "smallcaps": ("\\textsc{", "}"), "sup": ("$^{", "}$"), "sub": ("$_{", "}$"),
    "math": ("$", "$"), "alert": ("\\alert{", "}"),
}


def wrap(kind: str, text: str, arg: str = "") -> str:
    """``text`` formatted as ``kind`` (see :data:`_SIMPLE`, ``color``, ``size``, ``center``,
    ``left``, ``right``, ``itemize``, ``enumerate``, ``highlight``)."""
    t = text if text else "text"
    if kind in _SIMPLE:
        a, b = _SIMPLE[kind]
        return a + t + b
    if kind == "color":
        hexv = arg.lstrip("#").upper() or "C0392B"
        return f"\\textcolor[HTML]{{{hexv}}}{{{t}}}"
    if kind == "size":
        return "{\\" + (arg if arg in SIZES else "large") + " " + t + "}"
    if kind in ("center", "left", "right"):
        env = {"center": "center", "left": "flushleft", "right": "flushright"}[kind]
        return f"\\begin{{{env}}}\n{t}\n\\end{{{env}}}"
    if kind in ("itemize", "enumerate"):
        lines = [ln.strip() for ln in t.replace("\u2029", "\n").split("\n") if ln.strip()]
        body = "\n".join("  \\item " + ln for ln in lines)
        return f"\\begin{{{kind}}}\n{body}\n\\end{{{kind}}}"
    raise ValueError(f"unknown format {kind!r}")
