"""Journal layout templates for LaTeX documents (ACS / RSC / Elsevier + a plain article).

A template knows its preamble + front matter, the journal's figure widths and how to look in
Word. The layout of an *existing* document can be switched without touching its text:
:func:`extract_parts` pulls title / authors / abstract / body / extra packages out of the
source, :func:`build_document` writes them into another template. Pure text processing (no
Qt), so the skill, the GUI and the headless export share it.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path

# figure widths: (label, cm, spans both columns)
TEMPLATES = {
    "article": {
        "label": "Plain article (A4, 11 pt)",
        "class": "article",
        "two_column": False,
        "widths": [("Text width (full)", 16.0, False), ("Two thirds", 10.7, False),
                   ("Half", 8.0, False)],
        "word": {"font": "Times New Roman", "pt": 11, "margin_cm": 2.5, "line": 1.15},
        "preamble": r"""\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb}
\usepackage[margin=2.5cm]{geometry}
\usepackage{hyperref}
\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures
@EXTRA@""",
        "front": r"""\title{@TITLE@}
\author{@AUTHORS@}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
@ABSTRACT@
\end{abstract}
""",
        "ack": r"\section*{Acknowledgements}" "\n",
        "known": {"inputenc", "fontenc", "amsmath", "amssymb", "geometry", "hyperref",
                  "noorsuite"},
    },
    "acs": {
        "label": "ACS journal (achemso)",
        "class": "achemso",
        "two_column": False,
        "widths": [("ACS single column (8.25 cm)", 8.25, False),
                   ("ACS double column (17.78 cm)", 17.78, True)],
        "word": {"font": "Times New Roman", "pt": 12, "margin_cm": 2.54, "line": 2.0},
        "preamble": r"""\documentclass[journal=jacsat,manuscript=article]{achemso}
\usepackage{graphicx}
\usepackage[version=4]{mhchem}
\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures
@EXTRA@""",
        "front": r"""@ACSAUTHORS@
\title{@TITLE@}

\begin{document}

\begin{abstract}
@ABSTRACT@
\end{abstract}
""",
        "ack": "",
        "known": {"graphicx", "mhchem", "noorsuite", "achemso", "natbib", "hyperref"},
    },
    "rsc": {
        "label": "RSC journal (rsc.sty, two columns)",
        "class": "article",
        "two_column": True,
        "widths": [("RSC single column (8.3 cm)", 8.3, False),
                   ("RSC full width (17.1 cm)", 17.1, True)],
        "word": {"font": "Arial", "pt": 10, "margin_cm": 2.0, "line": 1.15},
        "preamble": r"""\documentclass[twoside,twocolumn,9pt]{article}
\usepackage[version=4]{mhchem}
\usepackage{rsc}   % loads natbib (superscript numbers) and the RSC look
\usepackage{graphicx}
\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures
@EXTRA@""",
        "front": r"""\begin{document}

\twocolumn[
  \begin{@twocolumnfalse}
  \noindent\LARGE{\textbf{@TITLE@}} \\
  \noindent\large{@AUTHORS@} \\
  \noindent\normalsize{@ABSTRACT@}
  \end{@twocolumnfalse}
]
""",
        "ack": r"\section*{Acknowledgements}" "\n",
        "known": {"mhchem", "rsc", "graphicx", "noorsuite", "natbib", "hyperref"},
    },
    "elsevier": {
        "label": "Elsevier journal (elsarticle)",
        "class": "elsarticle",
        "two_column": False,
        "widths": [("Elsevier single column (9 cm)", 9.0, False),
                   ("Elsevier 1.5 column (14 cm)", 14.0, False),
                   ("Elsevier double column (19 cm)", 19.0, True)],
        "word": {"font": "Times New Roman", "pt": 12, "margin_cm": 2.5, "line": 1.5},
        "preamble": r"""\documentclass[preprint,12pt]{elsarticle}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{hyperref}
\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures
@EXTRA@""",
        "front": r"""\begin{document}

\begin{frontmatter}
\title{@TITLE@}
@ELSAUTHORS@
\begin{abstract}
@ABSTRACT@
\end{abstract}
\begin{keyword}
keyword one \sep keyword two
\end{keyword}
\end{frontmatter}
""",
        "ack": r"\section*{Acknowledgements}" "\n",
        "known": {"amsmath", "amssymb", "graphicx", "hyperref", "noorsuite", "natbib"},
    },
}

ORDER = ["article", "acs", "rsc", "elsevier"]


# ------------------------------------------------------------------ user templates
def user_dir() -> Path:
    """Where the user's own layouts live (``$NOORSUITE_TEMPLATES`` or
    ``~/.noorsuite/templates``)."""
    d = os.environ.get("NOORSUITE_TEMPLATES")
    return Path(d) if d else Path(os.path.expanduser("~")) / ".noorsuite" / "templates"


def load_user_templates() -> dict:
    """``{"user:<slug>": template dict}`` of every readable ``*.json`` in :func:`user_dir`."""
    out = {}
    d = user_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            if t.get("preamble") and t.get("front"):
                t["known"] = set(t.get("known", []))
                t["widths"] = [tuple(w) for w in t.get("widths", [])]
                out["user:" + f.stem] = t
        except (OSError, ValueError):
            continue
    return out


def is_user(template_id) -> bool:
    return str(template_id or "").startswith("user:")


def list_templates() -> list:
    """``[(id, label)]``: the built-in journal layouts, then the user's own."""
    return [(i, TEMPLATES[i]["label"]) for i in ORDER] + \
           [(i, t["label"] + "  (my layout)") for i, t in load_user_templates().items()]


def get(template_id: str | None) -> dict:
    if is_user(template_id):
        t = load_user_templates().get(template_id)
        if t is not None:
            return t
    return TEMPLATES.get(template_id or "article", TEMPLATES["article"])


def base_of(template_id: str | None) -> str:
    """The built-in template a layout derives from (the id itself for built-ins)."""
    if is_user(template_id):
        return get(template_id).get("base", "article")
    return template_id if template_id in TEMPLATES else "article"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "layout"


def _parse_widths(widths) -> list:
    """``"8.5, 17.5"`` / ``[8.5, 17.5]`` / ``[(label, cm, span)]`` -> the widths list."""
    if not widths:
        return []
    if isinstance(widths, str):
        widths = [float(x) for x in re.split(r"[;, ]+", widths.strip()) if x]
    out = []
    for i, w in enumerate(widths):
        if isinstance(w, (tuple, list)):
            out.append((str(w[0]), float(w[1]), bool(w[2]) if len(w) > 2 else False))
        else:
            span = i == len(widths) - 1 and len(widths) > 1
            name = "Double column" if span else ("Single column" if i == 0 else "1.5 column")
            out.append((f"{name} ({float(w):g} cm)", float(w), span))
    return out


def make_user_template(source: str, label: str, widths=None) -> str:
    """Store ``source``'s layout (class, packages, macros -- not its text) as a reusable
    template; returns its id (``user:<slug>``). ``widths`` = figure widths in cm (a list or
    ``"8.5, 17.5"``: single column first, last = double column)."""
    m = re.search(r"\\begin\{document\}", source)
    if not m:
        raise ValueError("the document has no \\begin{document}")
    base = detect(source)
    b = TEMPLATES[base]
    pre, _ = _take_commands(source[:m.start()],
                            ["title", "author", "affiliation", "email", "address", "date"])
    pre = pre.rstrip() + "\n"
    if "{noorsuite}" not in pre:
        pre += "\\usepackage{noorsuite}   % \\noorsheet{...}: NoorSuite sheets as figures\n"
    known = set()
    for pm in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", pre):
        known |= {n.strip() for n in pm.group(1).split(",")}
    cls = re.search(r"\\documentclass(?:\[([^\]]*)\])?\{([^}]*)\}", pre)
    two = b["two_column"] or bool(cls and "twocolumn" in (cls.group(1) or ""))
    t = {"label": label.strip() or "My layout", "base": base, "class": cls.group(2) if cls else "",
         "two_column": two, "preamble": pre + "@EXTRA@", "front": b["front"],
         "tail": "\\end{document}\n",
         "widths": [list(w) for w in (_parse_widths(widths) or b["widths"])],
         "word": dict(b["word"]), "ack": b.get("ack", ""), "known": sorted(known)}
    d = user_dir()
    d.mkdir(parents=True, exist_ok=True)
    slug = _slug(t["label"])
    (d / f"{slug}.json").write_text(json.dumps(t, indent=2), encoding="utf-8")
    return "user:" + slug


def delete_user_template(template_id: str) -> bool:
    if not is_user(template_id):
        return False
    f = user_dir() / (template_id[5:] + ".json")
    try:
        f.unlink()
        return True
    except OSError:
        return False


def figure_widths(template_id: str | None) -> list:
    """``[(label, cm, double_column)]`` of the journal's figure widths."""
    return list(get(template_id)["widths"])


def detect(source: str) -> str:
    """Template id of a document's own class / packages (``"article"`` when unknown)."""
    code = "\n".join(re.sub(r"(?<!\\)%.*", "", ln) for ln in source.splitlines())
    m = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^}]*)\}", code)
    cls = m.group(1).strip() if m else ""
    if cls == "achemso":
        return "acs"
    if cls == "elsarticle":
        return "elsevier"
    if re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\brsc\b[^}]*\}", code):
        return "rsc"
    return "article"


def has_own_citations(source: str) -> bool:
    """True when the class / package brings natbib and its own reference look (achemso,
    elsarticle, rsc.sty) -- loading natbib again with other options would clash."""
    return detect(source) != "article"


# ----------------------------------------------------------------------- parsing
def _balanced(text: str, i: int):
    """Contents of the ``{...}`` group starting at ``text[i] == "{"`` and the index behind
    it, or ``None`` when unbalanced."""
    if i >= len(text) or text[i] != "{":
        return None
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j], j + 1
        j += 1
    return None


def _take_commands(text: str, names) -> tuple[str, dict]:
    """Remove every ``\\name[opt]{arg}`` for ``names`` from ``text``; returns the text and
    ``{name: [arg, ...]}``."""
    found = {n: [] for n in names}
    pat = re.compile(r"\\(" + "|".join(names) + r")\*?\s*(\[[^\]]*\])?\s*(?=\{)")
    out, pos = [], 0
    for m in pat.finditer(text):
        if m.start() < pos:
            continue
        grp = _balanced(text, m.end())
        if grp is None:
            continue
        found[m.group(1)].append(grp[0].strip())
        out.append(text[pos:m.start()])
        pos = grp[1]
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == "\n":
            pos += 1
    out.append(text[pos:])
    return "".join(out), found


_ENV = r"\\begin\{{{n}\}}(.*?)\\end\{{{n}\}}"


def extract_parts(source: str) -> dict:
    """``{title, authors[], abstract, body, extra}`` of a document in any of the templates.
    ``extra`` = user packages / one-line macros of the preamble that the template itself
    does not provide."""
    m = re.search(r"\\begin\{document\}", source)
    pre = source[:m.start()] if m else ""
    rest = source[m.end():] if m else source
    end = list(re.finditer(r"\\end\{document\}", rest))
    body = rest[:end[-1].start()] if end else rest
    # front matter commands live in the preamble (article / achemso) or in the body
    body, found_b = _take_commands(body, ["title", "author", "affiliation", "email", "address",
                                          "date", "tnoteref"])
    pre_clean, found_p = _take_commands(pre, ["title", "author", "affiliation", "email",
                                              "address", "date"])
    title = (found_p["title"] or found_b["title"] or [""])[0]
    authors = [re.sub(r"\\thanks\{[^}]*\}", "", a).strip()
               for a in (found_p["author"] + found_b["author"]) if a.strip()]
    # abstract: environment (article / achemso / elsarticle)
    am = re.search(_ENV.format(n="abstract"), body, re.S)
    abstract = am.group(1).strip() if am else ""
    if am:
        body = body[:am.start()] + body[am.end():]
    # twocolumn RSC front matter: title / author / abstract in a @twocolumnfalse block
    tc = re.search(r"\\twocolumn\[\s*\\begin\{@twocolumnfalse\}(.*?)\\end\{@twocolumnfalse\}\s*\]",
                   body, re.S)
    if tc:
        block = tc.group(1)
        t = re.search(r"\\LARGE\{\\textbf\{(.*?)\}\}", block, re.S)
        a = re.search(r"\\large\{(.*?)\}\s*\\\\", block, re.S)
        s = re.search(r"\\normalsize\{(.*?)\}\s*$", block.strip(), re.S)
        title = title or (t.group(1).strip() if t else "")
        if a and not authors:
            authors = [a.group(1).strip()]
        abstract = abstract or (s.group(1).strip() if s else "")
        body = body[:tc.start()] + body[tc.end():]
    body = re.sub(_ENV.format(n="frontmatter"), "", body, flags=re.S)
    body = re.sub(r"\\begin\{keyword\}.*?\\end\{keyword\}\s*", "", body, flags=re.S)
    body = re.sub(r"\\maketitle[ \t]*\n?", "", body)
    # extra preamble: packages the user added + one-line macro definitions
    old = TEMPLATES[detect(source)]["known"]
    extra = []
    for line in pre_clean.splitlines():
        s = line.strip()
        pm = re.match(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", s)
        if pm:
            names = {n.strip() for n in pm.group(1).split(",")}
            if names <= old or names & {"natbib", "achemso", "apalike", "apacite"}:
                continue
            extra.append(s)
        elif re.match(r"\\(newcommand|renewcommand|DeclareMathOperator|providecommand|def)\b", s) \
                and _balanced(s, s.find("{")) is not None and s.count("{") == s.count("}"):
            extra.append(s)
    return {"title": title, "authors": authors, "abstract": abstract,
            "body": body.strip("\n"), "extra": extra}


# ------------------------------------------------------------------------ building
def _split_authors(authors) -> list:
    out = []
    for a in authors or []:
        out += [p.strip() for p in re.split(r"\s*\\and\s*|\s+and\s+|;\s*", a) if p.strip()]
    return out


def _authors_for(tid: str, authors: list) -> dict:
    names = _split_authors(authors) or ["Author Name"]
    plain = (r" \and ".join(names)) if tid == "article" else " and ".join(names)
    acs = "\n".join(f"\\author{{{a}}}\n\\affiliation{{Institution, City, Country}}"
                    + ("\n\\email{author@example.org}" if i == 0 else "")
                    for i, a in enumerate(names))
    els = "\n".join(f"\\author{{{a}}}" for a in names) + "\n\\address{Institution, City, Country}"
    return {"@AUTHORS@": plain, "@ACSAUTHORS@": acs, "@ELSAUTHORS@": els}


def build_document(template_id: str, title: str = "Title", authors=None, abstract: str = "",
                   body: str = "", extra=None) -> str:
    t = get(template_id)
    tid = base_of(template_id)
    extra_txt = ("\n" + "\n".join(extra)) if extra else ""
    pre = t["preamble"].replace("@EXTRA@", extra_txt.lstrip("\n"))
    front = t["front"].replace("@TITLE@", title or "Title")
    front = front.replace("@ABSTRACT@", abstract or "Abstract.")
    for k, v in _authors_for(tid, list(authors or [])).items():
        front = front.replace(k, v)
    if not body.strip():
        body = ("\\section{Introduction}\nWrite your text here. Insert a figure with "
                "\\emph{Figures} in the side panel, or by hand:\n\\noorsheet{sheet-id}\n\n"
                "\\section{Results}\n")
    text = pre.rstrip("\n") + "\n\n"
    text += front.rstrip("\n") + "\n\n" + body.strip("\n") + "\n\n"
    text += t.get("tail", "\\end{document}\n")
    return text


def _fix_envs_for(tid: str, body: str) -> str:
    """The ACS-only environments become plain sections when leaving ``acs`` (and back)."""
    if tid != "acs":
        body = re.sub(r"\\begin\{acknowledgement\}(.*?)\\end\{acknowledgement\}",
                      lambda m: "\\section*{Acknowledgements}\n" + m.group(1).strip() + "\n",
                      body, flags=re.S)
        body = re.sub(r"\\begin\{suppinfo\}(.*?)\\end\{suppinfo\}",
                      lambda m: "\\section*{Supporting Information}\n" + m.group(1).strip() + "\n",
                      body, flags=re.S)
    else:
        body = re.sub(r"\\section\*\{Acknowledge?ments?\}\s*(.*?)(?=\\section|\\bibliograph|"
                      r"\\begin\{thebibliography\}|\Z)",
                      lambda m: "\\begin{acknowledgement}\n" + m.group(1).strip()
                      + "\n\\end{acknowledgement}\n\n", body, flags=re.S)
    return body


def apply_template(source: str, template_id: str) -> str:
    """The document's text in another layout: preamble and front matter are regenerated from
    title / authors / abstract, the body (text, figures, bibliography) is kept."""
    if template_id not in TEMPLATES and not (is_user(template_id)
                                             and template_id in load_user_templates()):
        return source
    parts = extract_parts(source)
    body = _fix_envs_for(base_of(template_id), parts["body"])
    return build_document(template_id, parts["title"] or "Title", parts["authors"],
                          parts["abstract"], body, parts["extra"])


def compose(source: str, template_id: str, title: str = "", authors=None,
            abstract: str = "") -> str:
    """For ``push_document(template=...)``: a complete source text in ``template_id`` from
    either a full document (switched) or just body text (wrapped)."""
    if r"\documentclass" in source:
        return apply_template(source, template_id)
    return build_document(template_id, title or "Title", authors, abstract, source)


# ------------------------------------------------------------------ Word look
def _set(xml: str, pattern: str, repl: str, count=1) -> str:
    return re.sub(pattern, repl, xml, count=count, flags=re.S)


def write_reference_docx(template_id: str, out_path, default_docx) -> bool:
    """A pandoc ``--reference-doc``: ``default_docx`` (pandoc's own) restyled with the
    template's font, size, line spacing and page margins. Returns False if it cannot."""
    w = get(template_id)["word"]
    try:
        src = zipfile.ZipFile(default_docx)
    except (OSError, zipfile.BadZipFile):
        return False
    half_pt = int(round(w["pt"] * 2))
    twips = int(round(w["margin_cm"] / 2.54 * 1440))
    line = int(round(240 * float(w["line"])))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "word/styles.xml":
                xml = data.decode("utf-8")
                font = w["font"]
                xml = re.sub(r'(<w:rFonts)[^>]*?/>',
                             rf'\1 w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}" '
                             rf'w:eastAsia="{font}"/>', xml, count=1)
                xml = _set(xml, r'(<w:docDefaults>.*?<w:rPr>.*?)<w:sz w:val="\d+"\s*/>',
                           rf'\1<w:sz w:val="{half_pt}"/>')
                # body text style: size + line spacing
                def normal(m):
                    block = m.group(0)
                    block = re.sub(r'<w:sz w:val="\d+"\s*/>', f'<w:sz w:val="{half_pt}"/>', block)
                    if "<w:spacing" in block:
                        block = re.sub(r'<w:spacing[^>]*/>',
                                       f'<w:spacing w:line="{line}" w:lineRule="auto"/>', block, count=1)
                    elif "<w:pPr>" in block:
                        block = block.replace(
                            "<w:pPr>", f'<w:pPr><w:spacing w:line="{line}" w:lineRule="auto"/>', 1)
                    else:
                        block = block.replace(
                            "</w:style>", f'<w:pPr><w:spacing w:line="{line}" '
                            f'w:lineRule="auto"/></w:pPr></w:style>')
                    return block
                xml = re.sub(r'<w:style [^>]*w:styleId="(?:BodyText|FirstParagraph)".*?</w:style>',
                             normal, xml, flags=re.S)
                data = xml.encode("utf-8")
            elif item.filename == "word/document.xml":
                xml = data.decode("utf-8")
                mar = (f'<w:pgMar w:top="{twips}" w:right="{twips}" w:bottom="{twips}" '
                       f'w:left="{twips}" w:header="708" w:footer="708" w:gutter="0"/>')
                if "<w:pgMar" in xml:
                    xml = re.sub(r"<w:pgMar[^>]*/>", mar, xml)
                elif "<w:sectPr" in xml:
                    xml = re.sub(r"(<w:sectPr[^>]*>)", r"\1" + mar, xml, count=1)
                else:
                    xml = xml.replace("</w:body>", f"<w:sectPr>{mar}</w:sectPr></w:body>")
                data = xml.encode("utf-8")
            dst.writestr(item, data)
    return Path(out_path).is_file()
