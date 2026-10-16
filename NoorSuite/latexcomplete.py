"""LaTeX autocompletion: what the editor offers while typing (Qt-free, so it is easy to test).

``complete(text_before_cursor, extras)`` -> ``(start, items)``: ``start`` is the index in the text where
the typed prefix begins (the completion replaces ``text[start:cursor]``), ``items`` are :class:`Item`
objects sorted best-first. ``expand(snippet, indent)`` turns a snippet (``$1``, ``${2:default}``, ``$0``)
into plain text plus the tab stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Item:
    label: str                 # shown in the list
    insert: str                # snippet that replaces the typed prefix
    detail: str = ""
    kind: str = "command"


# --------------------------------------------------------------------------- catalogues
# (name, snippet, detail)  -- "\\" + name is the typed command
COMMANDS = [
    # structure
    ("documentclass", r"\documentclass[$2]{${1:article}}$0", "document class"),
    ("usepackage", r"\usepackage{$1}$0", "load a package"),
    ("begin", r"\begin{", "environment"),
    ("end", r"\end{", "close an environment"),
    ("title", r"\title{$1}$0", ""), ("author", r"\author{$1}$0", ""), ("date", r"\date{$1}$0", ""),
    ("maketitle", r"\maketitle", ""), ("tableofcontents", r"\tableofcontents", ""),
    ("part", r"\part{$1}$0", ""), ("chapter", r"\chapter{$1}$0", ""),
    ("section", r"\section{$1}$0", ""), ("subsection", r"\subsection{$1}$0", ""),
    ("subsubsection", r"\subsubsection{$1}$0", ""), ("paragraph", r"\paragraph{$1}$0", ""),
    ("section*", r"\section*{$1}$0", "unnumbered"), ("subsection*", r"\subsection*{$1}$0", "unnumbered"),
    ("appendix", r"\appendix", ""), ("newpage", r"\newpage", ""), ("clearpage", r"\clearpage", ""),
    ("input", r"\input{$1}$0", "include a file"), ("include", r"\include{$1}$0", ""),
    # text
    ("textbf", r"\textbf{$1}$0", "bold"), ("textit", r"\textit{$1}$0", "italic"),
    ("emph", r"\emph{$1}$0", "emphasis"), ("underline", r"\underline{$1}$0", ""),
    ("texttt", r"\texttt{$1}$0", "typewriter"), ("textsc", r"\textsc{$1}$0", "small caps"),
    ("textsf", r"\textsf{$1}$0", "sans serif"), ("textrm", r"\textrm{$1}$0", "roman"),
    ("textcolor", r"\textcolor{${1:red}}{$2}$0", "coloured text"),
    ("textsuperscript", r"\textsuperscript{$1}$0", ""), ("textsubscript", r"\textsubscript{$1}$0", ""),
    ("footnote", r"\footnote{$1}$0", ""), ("url", r"\url{$1}$0", ""), ("href", r"\href{$1}{$2}$0", "link"),
    ("color", r"\color{$1}$0", ""), ("colorbox", r"\colorbox{$1}{$2}$0", ""),
    ("centering", r"\centering", ""), ("raggedright", r"\raggedright", ""),
    ("noindent", r"\noindent", ""), ("hfill", r"\hfill", ""), ("vfill", r"\vfill", ""),
    ("vspace", r"\vspace{${1:1em}}$0", ""), ("hspace", r"\hspace{${1:1em}}$0", ""),
    ("quad", r"\quad", ""), ("qquad", r"\qquad", ""), ("newline", r"\newline", ""),
    ("tiny", r"\tiny", ""), ("scriptsize", r"\scriptsize", ""), ("footnotesize", r"\footnotesize", ""),
    ("small", r"\small", ""), ("normalsize", r"\normalsize", ""), ("large", r"\large", ""),
    ("Large", r"\Large", ""), ("LARGE", r"\LARGE", ""), ("huge", r"\huge", ""), ("Huge", r"\Huge", ""),
    ("bfseries", r"\bfseries", ""), ("itshape", r"\itshape", ""), ("sffamily", r"\sffamily", ""),
    # lists / floats / references
    ("item", r"\item $0", "list item"), ("label", r"\label{${1:fig:}}$0", ""),
    ("ref", r"\ref{$1}$0", "cross reference"), ("eqref", r"\eqref{$1}$0", "(equation)"),
    ("autoref", r"\autoref{$1}$0", ""), ("pageref", r"\pageref{$1}$0", ""),
    ("cite", r"\cite{$1}$0", "citation"), ("citep", r"\citep{$1}$0", "(Author, year)"),
    ("citet", r"\citet{$1}$0", "Author (year)"), ("caption", r"\caption{$1}$0", ""),
    ("includegraphics", r"\includegraphics[width=${1:0.8\linewidth}]{$2}$0", "picture"),
    ("bibliography", r"\bibliography{$1}$0", ""), ("bibliographystyle", r"\bibliographystyle{$1}$0", ""),
    ("hline", r"\hline", ""), ("toprule", r"\toprule", ""), ("midrule", r"\midrule", ""),
    ("bottomrule", r"\bottomrule", ""), ("multicolumn", r"\multicolumn{$1}{$2}{$3}$0", ""),
    ("newcommand", r"\newcommand{\$1}[${2:0}]{$3}$0", "define a macro"),
    ("renewcommand", r"\renewcommand{\$1}{$2}$0", ""),
    ("definecolor", r"\definecolor{$1}{${2:HTML}}{$3}$0", ""),
    ("setlength", r"\setlength{$1}{$2}$0", ""),
    # math
    ("frac", r"\frac{$1}{$2}$0", "fraction"), ("dfrac", r"\dfrac{$1}{$2}$0", ""),
    ("sqrt", r"\sqrt{$1}$0", ""), ("sum", r"\sum_{${1:i=1}}^{${2:n}}$0", "sum"),
    ("prod", r"\prod_{${1:i=1}}^{${2:n}}$0", ""), ("int", r"\int_{${1:a}}^{${2:b}} $3\,\mathrm{d}${4:x}$0", "integral"),
    ("lim", r"\lim_{${1:x\to\infty}}$0", ""), ("partial", r"\partial", "\u2202"),
    ("left", r"\left($1\right)$0", ""), ("right", r"\right", ""),
    ("mathrm", r"\mathrm{$1}$0", ""), ("mathbf", r"\mathbf{$1}$0", ""), ("mathcal", r"\mathcal{$1}$0", ""),
    ("mathbb", r"\mathbb{$1}$0", ""), ("text", r"\text{$1}$0", "text in math"),
    ("hat", r"\hat{$1}$0", ""), ("bar", r"\bar{$1}$0", ""), ("vec", r"\vec{$1}$0", ""),
    ("dot", r"\dot{$1}$0", ""), ("ddot", r"\ddot{$1}$0", ""), ("tilde", r"\tilde{$1}$0", ""),
    ("overline", r"\overline{$1}$0", ""), ("underbrace", r"\underbrace{$1}_{$2}$0", ""),
    ("cdot", r"\cdot", "\u00b7"), ("times", r"\times", "\u00d7"), ("pm", r"\pm", "\u00b1"),
    ("leq", r"\leq", "\u2264"), ("geq", r"\geq", "\u2265"), ("neq", r"\neq", "\u2260"),
    ("approx", r"\approx", "\u2248"), ("propto", r"\propto", "\u221d"), ("infty", r"\infty", "\u221e"),
    ("rightarrow", r"\rightarrow", "\u2192"), ("to", r"\to", "\u2192"), ("Rightarrow", r"\Rightarrow", "\u21d2"),
    ("leftrightarrow", r"\leftrightarrow", "\u2194"), ("ldots", r"\ldots", "\u2026"), ("cdots", r"\cdots", "\u22ef"),
    ("nabla", r"\nabla", "\u2207"), ("degree", r"\degree", "\u00b0"), ("SI", r"\SI{$1}{$2}$0", "siunitx quantity"),
    ("si", r"\si{$1}$0", "siunitx unit"), ("num", r"\num{$1}$0", "siunitx number"),
    ("ce", r"\ce{$1}$0", "mhchem formula"),
    # greek
    ("alpha", r"\alpha", "\u03b1"), ("beta", r"\beta", "\u03b2"), ("gamma", r"\gamma", "\u03b3"),
    ("delta", r"\delta", "\u03b4"), ("epsilon", r"\epsilon", "\u03b5"), ("varepsilon", r"\varepsilon", "\u03b5"),
    ("zeta", r"\zeta", "\u03b6"), ("eta", r"\eta", "\u03b7"), ("theta", r"\theta", "\u03b8"),
    ("kappa", r"\kappa", "\u03ba"), ("lambda", r"\lambda", "\u03bb"), ("mu", r"\mu", "\u03bc"),
    ("nu", r"\nu", "\u03bd"), ("xi", r"\xi", "\u03be"), ("pi", r"\pi", "\u03c0"), ("rho", r"\rho", "\u03c1"),
    ("sigma", r"\sigma", "\u03c3"), ("tau", r"\tau", "\u03c4"), ("phi", r"\phi", "\u03c6"),
    ("varphi", r"\varphi", "\u03c6"), ("chi", r"\chi", "\u03c7"), ("psi", r"\psi", "\u03c8"),
    ("omega", r"\omega", "\u03c9"), ("Gamma", r"\Gamma", "\u0393"), ("Delta", r"\Delta", "\u0394"),
    ("Theta", r"\Theta", "\u0398"), ("Lambda", r"\Lambda", "\u039b"), ("Sigma", r"\Sigma", "\u03a3"),
    ("Phi", r"\Phi", "\u03a6"), ("Psi", r"\Psi", "\u03a8"), ("Omega", r"\Omega", "\u03a9"),
    # NoorSuite
    ("noorsheet", r"\noorsheet[width=${1:0.8\linewidth}]{$2}$0", "NoorSuite graph"),
    ("noorfile", r"\noorfile[width=${1:0.6\linewidth}]{$2}$0", "SVG / picture"),
    ("noortable", r"\noortable{$1}$0", "data table"),
    ("noorobj", r"\noorobj[x=${1:2},y=${2:2},w=${3:4},h=${4:2}]{${5:rect}}{$6}$0", "free slide object"),
    ("noormasterobj", r"\noormasterobj[x=${1:0},y=${2:0},w=${3:4},h=${4:1}]{${5:rect}}{$6}$0", "object on every slide"),
    ("noorslide", r"\noorslide{$1}$0", "slide setup"),
    ("framesource", r"\framesource{$1}$0", "source line of a slide"),
    # beamer
    ("frametitle", r"\frametitle{$1}$0", ""), ("pause", r"\pause", "next click"),
    ("uncover", r"\uncover<${1:2->}>{$2}$0", "appear at step"), ("only", r"\only<${1:2}>{$2}$0", "only at step"),
    ("visible", r"\visible<${1:2->}>{$2}$0", ""), ("onslide", r"\onslide<${1:2->}>$0", ""),
    ("alert", r"\alert{$1}$0", "highlight"), ("note", r"\note{$1}$0", "speaker note"),
    ("titlepage", r"\titlepage", ""), ("transfade", r"\transfade", "transition"),
    ("transdissolve", r"\transdissolve", "transition"), ("transwipe", r"\transwipe", "transition"),
    ("setbeamercolor", r"\setbeamercolor{$1}{fg=$2,bg=$3}$0", ""), ("setbeamerfont", r"\setbeamerfont{$1}{size=$2}$0", ""),
    ("setbeamercovered", r"\setbeamercovered{transparent}", "dim hidden content"),
    ("usetheme", r"\usetheme{$1}$0", ""), ("usecolortheme", r"\usecolortheme{$1}$0", ""),
]

# environment -> snippet that starts right after "\begin{" (name, closing brace, body, \end)
ENVIRONMENTS = {
    "equation": ("equation}\n  $1\n  \\label{eq:$2}\n\\end{equation}$0", "numbered equation"),
    "equation*": ("equation*}\n  $1\n\\end{equation*}$0", "unnumbered equation"),
    "align": ("align}\n  $1 &= $2 \\\\\n  $3 &= $4\n\\end{align}$0", "aligned equations"),
    "align*": ("align*}\n  $1 &= $2\n\\end{align*}$0", ""),
    "gather": ("gather}\n  $1 \\\\\n  $2\n\\end{gather}$0", ""),
    "multline": ("multline}\n  $1\n\\end{multline}$0", ""),
    "cases": ("cases}\n  $1 & \\text{if } $2 \\\\\n  $3 & \\text{otherwise}\n\\end{cases}$0", ""),
    "matrix": ("matrix}\n  $1 & $2 \\\\\n  $3 & $4\n\\end{matrix}$0", ""),
    "pmatrix": ("pmatrix}\n  $1 & $2 \\\\\n  $3 & $4\n\\end{pmatrix}$0", ""),
    "figure": ("figure}[htbp]\n  \\centering\n  $1\n  \\caption{$2}\n  \\label{fig:$3}\n\\end{figure}$0", "float"),
    "figure*": ("figure*}[htbp]\n  \\centering\n  $1\n  \\caption{$2}\n  \\label{fig:$3}\n\\end{figure*}$0", ""),
    "table": ("table}[htbp]\n  \\centering\n  \\caption{$1}\n  \\label{tab:$2}\n  $3\n\\end{table}$0", "float"),
    "tabular": ("tabular}{${1:ll}}\n  \\toprule\n  $2 \\\\\n  \\midrule\n  $3 \\\\\n  \\bottomrule\n\\end{tabular}$0", ""),
    "itemize": ("itemize}\n  \\item $1\n\\end{itemize}$0", "bullet list"),
    "enumerate": ("enumerate}\n  \\item $1\n\\end{enumerate}$0", "numbered list"),
    "description": ("description}\n  \\item[$1] $2\n\\end{description}$0", ""),
    "abstract": ("abstract}\n  $1\n\\end{abstract}$0", ""),
    "center": ("center}\n  $1\n\\end{center}$0", ""),
    "flushleft": ("flushleft}\n  $1\n\\end{flushleft}$0", ""),
    "flushright": ("flushright}\n  $1\n\\end{flushright}$0", ""),
    "quote": ("quote}\n  $1\n\\end{quote}$0", ""),
    "verbatim": ("verbatim}\n$1\n\\end{verbatim}$0", ""),
    "minipage": ("minipage}{${1:0.48\\textwidth}}\n  $2\n\\end{minipage}$0", ""),
    "tikzpicture": ("tikzpicture}\n  $1\n\\end{tikzpicture}$0", ""),
    "document": ("document}\n$1\n\\end{document}$0", ""),
    "frame": ("frame}{${1:Title}}\n  $2\n\\end{frame}$0", "slide"),
    "columns": ("columns}[T]\n  \\begin{column}{0.48\\textwidth}\n    $1\n  \\end{column}\n"
                "  \\begin{column}{0.48\\textwidth}\n    $2\n  \\end{column}\n\\end{columns}$0", "two columns"),
    "column": ("column}{${1:0.48\\textwidth}}\n  $2\n\\end{column}$0", ""),
    "block": ("block}{${1:Title}}\n  $2\n\\end{block}$0", ""),
    "alertblock": ("alertblock}{${1:Title}}\n  $2\n\\end{alertblock}$0", ""),
    "exampleblock": ("exampleblock}{${1:Title}}\n  $2\n\\end{exampleblock}$0", ""),
    "theorem": ("theorem}\n  $1\n\\end{theorem}$0", ""),
    "proof": ("proof}\n  $1\n\\end{proof}$0", ""),
}

PACKAGES = [
    "amsmath", "amssymb", "amsthm", "array", "babel", "biblatex", "booktabs", "caption", "cleveref",
    "color", "enumitem", "fancyhdr", "float", "fontenc", "fontspec", "geometry", "graphicx", "hyperref",
    "inputenc", "lmodern", "mathtools", "mhchem", "microtype", "multicol", "multirow", "natbib",
    "noorsuite", "pgfplots", "physics", "setspace", "siunitx", "subcaption", "tabularx", "tikz",
    "titlesec", "ulem", "url", "xcolor", "xspace", "achemso", "rsc", "authblk", "etoolbox",
    "listings", "algorithm2e", "bm", "cancel", "csquotes", "epstopdf", "lipsum", "makecell",
    "longtable", "pdflscape", "rotating", "soul", "threeparttable", "wrapfig",
]
CLASSES = ["article", "report", "book", "beamer", "letter", "achemso", "elsarticle", "revtex4-2",
           "scrartcl", "scrreprt", "scrbook", "standalone", "memoir"]
OBJ_KEYS = ["x=", "y=", "w=", "h=", "x2=", "y2=", "angle=", "fill=", "fill2=", "grad=", "line=", "lw=",
            "opacity=", "size=", "color=", "align=", "dash="]
OBJ_KINDS = ["rect", "ellipse", "line", "arrow", "text", "sheet", "file"]
FIG_KEYS = ["width=", "height=", "scale=", "angle=", "xshift=", "yshift=", "keepaspectratio",
            "trim=", "clip"]


# --------------------------------------------------------------------------- snippets
_STOP = re.compile(r"\$\{(\d+):([^}]*)\}|\$(\d+)")


def expand(snippet: str, indent: str = "") -> tuple:
    """Snippet -> ``(text, stops)``; ``stops`` = ``[(start, end)]`` in tab order, the ``$0`` stop (or the
    end of the text) last. Newlines get ``indent`` appended."""
    text = ""
    found = []
    pos = 0

    def lit(piece: str) -> str:
        return piece.replace("\n", "\n" + indent)
    for m in _STOP.finditer(snippet):
        text += lit(snippet[pos:m.start()])
        if m.group(1) is not None:
            num, default = int(m.group(1)), lit(m.group(2))
        else:
            num, default = int(m.group(3)), ""
        found.append((num, len(text), len(text) + len(default)))
        text += default
        pos = m.end()
    text += lit(snippet[pos:])
    order = sorted((f for f in found if f[0] > 0), key=lambda f: f[0])
    zero = [f for f in found if f[0] == 0]
    stops = [(s, e) for _n, s, e in order]
    stops.append((zero[0][1], zero[0][2]) if zero else (len(text), len(text)))
    return text, stops


# --------------------------------------------------------------------------- context
_ENV_ARG = re.compile(r"\\(begin|end)\{([A-Za-z*]*)$")
_ARG = re.compile(r"\\(cite[a-zA-Z]*|ref|eqref|autoref|cref|pageref|noorsheet|noorfile|noortable|"
                  r"usepackage|documentclass|usetheme)\s*(?:\[[^\]]*\])?\{([^{}]*)$")
_OPTS = re.compile(r"\\(noorobj|noormasterobj|noorsheet|noorfile|includegraphics)\[([^\]]*)$")
_CMD = re.compile(r"\\([A-Za-z@]*\*?)$")


def _unclosed_envs(before: str) -> list:
    stack = []
    for m in re.finditer(r"\\(begin|end)\{([A-Za-z*]+)\}", before):
        if m.group(1) == "begin":
            stack.append(m.group(2))
        elif m.group(2) in stack:
            while stack and stack.pop() != m.group(2):
                pass
    return stack


def labels_in(source: str) -> list:
    return sorted(set(re.findall(r"\\label\{([^{}]+)\}", source)))


def _rank(items, prefix):
    p = prefix.lower()
    starts = [i for i in items if i[0].lower().startswith(p)]
    inside = [i for i in items if p in i[0].lower() and i not in starts]
    return starts + inside


def complete(before: str, extras: dict | None = None, limit: int = 40):
    """-> ``(start, [Item])`` for the text in front of the cursor, or ``(len(before), [])``."""
    extras = extras or {}
    line_start = before.rfind("\n") + 1
    line = before[line_start:]
    if re.search(r"(?<!\\)%", line):                       # inside a comment
        return len(before), []
    m = _ENV_ARG.search(before)
    if m:
        start = m.start(2)
        prefix = m.group(2)
        if m.group(1) == "end":
            names = list(reversed(_unclosed_envs(before[:m.start()])))
            items = [Item(n, n + "}", "close", "env") for n in names if n.lower().startswith(prefix.lower())]
            return start, items[:limit]
        pool = [(n, snip, det) for n, (snip, det) in ENVIRONMENTS.items()]
        ranked = _rank(pool, prefix)
        return start, [Item(n, snip, det, "env") for n, snip, det in ranked[:limit]]
    m = _ARG.search(before)
    if m:
        cmd, typed = m.group(1), m.group(2)
        token = typed.split(",")[-1].lstrip() if cmd.startswith("cite") or cmd == "usepackage" else typed
        start = len(before) - len(token)
        pool = []
        if cmd.startswith("cite"):
            pool = [(k, k, d) for k, d in extras.get("cite", [])]
        elif cmd in ("ref", "eqref", "autoref", "cref", "pageref"):
            pool = [(k, k, "label") for k in extras.get("labels", [])]
        elif cmd == "noorsheet":
            pool = [(k, k, n) for k, n in extras.get("sheets", [])]
        elif cmd == "noorfile":
            pool = [(k, k, n) for k, n in extras.get("assets", [])]
        elif cmd == "noortable":
            pool = [(k, k, n) for k, n in extras.get("tables", [])]
        elif cmd == "usepackage":
            pool = [(p, p, "package") for p in PACKAGES]
        elif cmd == "documentclass":
            pool = [(c, c, "class") for c in CLASSES]
        elif cmd == "usetheme":
            pool = [(t, t, "theme") for t in ["default", "Madrid", "Warsaw", "metropolis", "noorclean"]]
        return start, [Item(a, b, c, "arg") for a, b, c in _rank(pool, token)[:limit]]
    m = _OPTS.search(before)
    if m:
        cmd, typed = m.group(1), m.group(2)
        token = typed.split(",")[-1].lstrip()
        if "=" in token and token.split("=")[0] in ("grad", "align", "dash"):
            vals = {"grad": ["vertical", "horizontal", "radial"], "align": ["left", "center", "right"],
                    "dash": ["solid", "dashed", "dotted"]}[token.split("=")[0]]
            key, val = token.split("=", 1)
            start = len(before) - len(val)
            return start, [Item(v, v, key, "arg") for v in vals if v.startswith(val)]
        if "=" in token:
            return len(before), []
        pool = OBJ_KEYS if cmd in ("noorobj", "noormasterobj") else FIG_KEYS
        start = len(before) - len(token)
        return start, [Item(k, k, "", "arg") for k in pool if k.startswith(token)][:limit]
    if re.search(r"\\(noorobj|noormasterobj)(\[[^\]]*\])\{[a-z]*$", before):
        mm = re.search(r"\{([a-z]*)$", before)
        return mm.start(1), [Item(k, k, "", "arg") for k in OBJ_KINDS if k.startswith(mm.group(1))]
    m = _CMD.search(before)
    if m and (m.start() == 0 or before[m.start() - 1] != "\\") and len(m.group(1)) >= 1:
        prefix = m.group(1)
        pool = [(name, snip, det) for name, snip, det in COMMANDS]
        ranked = _rank(pool, prefix)
        return m.start(), [Item("\\" + n, s, d, "command") for n, s, d in ranked[:limit]]
    return len(before), []
