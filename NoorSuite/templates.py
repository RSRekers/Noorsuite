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
@AUTHORBLOCK@
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
  @RSCAFFIL@
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
    info = parse_authors(source)
    names = ["title", "author", "affiliation", "alsoaffiliation", "affil", "email", "ead",
             "cortext", "address", "date", "tnoteref"]
    body, found_b = _take_commands(body, names)
    pre_clean, found_p = _take_commands(pre, names)
    title = (found_p["title"] or found_b["title"] or [""])[0]
    authors = [a["name"] for a in info["authors"]] or \
        [re.sub(r"\\thanks\{[^}]*\}", "", a).strip()
         for a in (found_p["author"] + found_b["author"]) if a.strip()]
    affiliations = dict(info["affiliations"])
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
            authors = [_clean_name(x) for x in re.split(r"\s+and\s+|,\s*", a.group(1)) if x.strip()]
            info = {"authors": [{"name": n, "affils": [], "email": "", "corresponding": False}
                                for n in authors], "affiliations": {}}
        for k, t in re.findall(r"\\footnotesize\{\$\^\{([^}]*)\}\$\s*(.*?)\}", block):
            affiliations.setdefault(k.strip(), " ".join(t.split()))
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
            if names <= old or names & {"natbib", "achemso", "apalike", "apacite", "authblk"}:
                continue
            extra.append(s)
        elif re.match(r"\\(newcommand|renewcommand|DeclareMathOperator|providecommand|def)\b", s) \
                and _balanced(s, s.find("{")) is not None and s.count("{") == s.count("}"):
            extra.append(s)
    if info["authors"] and affiliations and not info["affiliations"]:
        # RSC block: superscript keys on the names are the link
        for a in info["authors"]:
            a["affils"] = [k for k in affiliations if k in (a.get("keys") or [])]
        info = {"authors": info["authors"], "affiliations": affiliations}
    return {"title": title, "authors": authors, "abstract": abstract,
            "body": body.strip("\n"), "extra": extra, "author_info": info["authors"],
            "affiliations": affiliations or info["affiliations"]}


# ----------------------------------------------------------------- author lists
_AUTHOR_CMDS = ("author", "affil", "affiliation", "alsoaffiliation", "address", "email",
                "ead", "cortext", "thanks")
_AUTH_TOKEN = re.compile(r"\\(" + "|".join(sorted(_AUTHOR_CMDS, key=len, reverse=True)) +
                         r")\*?\s*(\[[^\]]*\])?\s*(?=\{)")


def _clean_name(text: str) -> str:
    text = re.sub(r"\\(?:thanks|corref|tnoteref|fnref)\s*(?:\[[^\]]*\])?\{[^}]*\}", "", text)
    text = re.sub(r"\$\^\{?[^}$]*\}?\$|\\textsuperscript\{[^}]*\}", "", text)
    return " ".join(text.split())


def _front_text(source: str) -> str:
    """The part of a document where title / author commands live (before the first section,
    abstract or end of ``frontmatter``)."""
    stops = [m.start() for m in (re.search(p, source) for p in (
        r"\\begin\{abstract\}", r"\\section\b", r"\\end\{frontmatter\}", r"\\maketitle")) if m]
    return source[:min(stops)] if stops else source


def parse_authors(source: str) -> dict:
    r"""``{"authors": [{name, affils[keys], email, corresponding}], "affiliations": {key: text}}``
    read from the front matter of a document in *any* convention: authblk
    (``\author[1,2]{..}`` + ``\affil[1]{..}``), elsarticle (``\author[1]{..}`` + ``\address[1]{..}`` /
    ``\affiliation[1]{..}``), achemso (``\author{..}`` followed by ``\affiliation{..}`` /
    ``\alsoaffiliation{..}`` / ``\email{..}``) and plain ``\author{A \and B}``."""
    text = _COMMENT_STRIP.sub("", _front_text(source))
    authors, affils, cur, auto, pos = [], {}, None, 0, 0
    for m in _AUTH_TOKEN.finditer(text):
        if m.start() < pos:
            continue
        grp = _balanced(text, m.end())
        if grp is None:
            continue
        pos = grp[1]
        cmd, opt, arg = m.group(1), (m.group(2) or "")[1:-1].strip(), grp[0].strip()
        if cmd == "author":
            names = [_clean_name(p) for p in re.split(r"\s*\\and\s*", arg)] \
                if r"\and" in arg else [_clean_name(arg)]
            for nm in names:
                if not nm:
                    continue
                keys = [k.strip().rstrip("*") for k in opt.split(",") if k.strip().rstrip("*")]
                cur = {"name": nm, "affils": keys, "email": "", "corresponding": "*" in opt}
                em = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", arg)
                if em:                                    # 	hanks{Corresponding author: ...}
                    cur["email"], cur["corresponding"] = em.group(0), True
                authors.append(cur)
        elif cmd in ("affil", "address", "affiliation", "alsoaffiliation"):
            body = re.sub(r"organization=\{?([^,}]*)\}?", r"\1", arg)
            if opt and opt.strip("*"):
                affils[opt.strip()] = " ".join(body.split())
            elif cur is not None:                         # achemso: belongs to the last author
                auto += 1
                key = f"a{auto}"
                affils[key] = " ".join(body.split())
                cur["affils"].append(key)
            else:
                auto += 1
                affils[f"a{auto}"] = " ".join(body.split())
        elif cmd in ("email", "ead") and cur is not None:
            cur["email"] = arg
            cur["corresponding"] = True
    return {"authors": authors, "affiliations": affils}


_COMMENT_STRIP = re.compile(r"(?<!\\)%.*")


def _norm_authors(authors, affiliations=None):
    """Author entries as dicts + the affiliation map, with affiliation keys renumbered
    1..n in order of first use (what every convention prints)."""
    items = []
    for a in authors or []:
        if isinstance(a, dict):
            d = {"name": str(a.get("name", "")).strip(), "affils": [str(k) for k in
                                                                    a.get("affils", [])],
                 "email": str(a.get("email", "")).strip(),
                 "corresponding": bool(a.get("corresponding"))}
            if d["name"]:
                items.append(d)
        else:
            for nm in _split_authors([a]):
                items.append({"name": nm, "affils": [], "email": "", "corresponding": False})
    old = dict(affiliations or {})
    order = []
    for it in items:
        it["affils"] = [k for k in it["affils"] if k in old]
        for k in it["affils"]:
            if k not in order:
                order.append(k)
    ren = {k: str(i + 1) for i, k in enumerate(order)}
    for it in items:
        it["affils"] = [ren[k] for k in it["affils"]]
    return items, {ren[k]: old[k] for k in order}


def render_author_block(tid: str, authors, affiliations=None) -> dict:
    """Placeholder texts for a template's front matter + preamble lines the block needs."""
    items, aff = _norm_authors(authors, affiliations)
    names = [i["name"] for i in items] or ["Author Name"]
    extra = []
    sup = lambda ks: (r"$^{" + ",".join(ks) + "}$") if ks else ""          # noqa: E731
    # --- plain / article
    if aff:
        lines = []
        for it in items:
            k = ",".join(it["affils"])
            thanks = (f"\\thanks{{Corresponding author: {it['email']}}}"
                      if it["corresponding"] and it["email"] else "")
            lines.append(f"\\author[{k}]{{{it['name']}{thanks}}}" if k
                         else f"\\author{{{it['name']}{thanks}}}")
        lines += [f"\\affil[{k}]{{{t}}}" for k, t in aff.items()]
        article = "\n".join(lines)
        if tid == "article":
            extra.append(r"\usepackage{authblk}")
    else:
        article = r"\author{" + r" \and ".join(names) + "}"
    # --- achemso
    acs = []
    for i, it in enumerate(items or [{"name": names[0], "affils": [], "email": ""}]):
        acs.append(f"\\author{{{it['name']}}}")
        keys = it["affils"]
        if keys:
            acs.append(f"\\affiliation{{{aff[keys[0]]}}}")
            acs += [f"\\alsoaffiliation{{{aff[k]}}}" for k in keys[1:]]
        else:
            acs.append(r"\affiliation{Institution, City, Country}")
        if it.get("email") or i == 0:
            acs.append(f"\\email{{{it.get('email') or 'author@example.org'}}}")
    # --- elsarticle
    els = []
    for it in items or [{"name": names[0], "affils": [], "email": "", "corresponding": False}]:
        k = ",".join(it["affils"])
        mark = r"\corref{cor1}" if it.get("corresponding") else ""
        els.append(f"\\author[{k}]{{{it['name']}{mark}}}" if k else f"\\author{{{it['name']}{mark}}}")
        if it.get("email"):
            els.append(f"\\ead{{{it['email']}}}")
    if aff:
        els += [f"\\address[{k}]{{{t}}}" for k, t in aff.items()]
    else:
        els.append(r"\address{Institution, City, Country}")
    if any(i.get("corresponding") for i in items):
        els.append(r"\cortext[cor1]{Corresponding author}")
    # --- rsc: names with superscript keys + affiliation lines
    rsc_names = " and ".join(i["name"] + sup(i["affils"]) for i in items) or names[0]
    rsc_aff = "\n  ".join(f"\\noindent\\footnotesize{{$^{{{k}}}$ {t}}} \\\\"
                          for k, t in aff.items())
    plain = (r" \and " if tid == "article" else " and ").join(names)
    return {"@AUTHORBLOCK@": article, "@AUTHORS@": rsc_names if tid == "rsc" else plain,
            "@ACSAUTHORS@": "\n".join(acs), "@ELSAUTHORS@": "\n".join(els),
            "@RSCAFFIL@": rsc_aff, "_extra": extra}


_FRONT_CMD_LINE = re.compile(
    r"^[ \t]*\\(?:title|author|affil|affiliation|alsoaffiliation|address|email|ead|date)\*?\s*"
    r"(?:\[[^\]]*\])?\s*\{")


def _close_front_braces(source: str):
    r"""A title / author / affiliation line whose ``{`` is never closed (the next line is another
    command) is closed at the end of the line in the built copy -- LaTeX otherwise stops with
    "File ended while scanning use of \affil". Returns ``(source, notes)``."""
    lines = source.split("\n")
    stop = next((i for i, ln in enumerate(lines)
                 if re.search(r"\\begin\{document\}", ln)), len(lines))
    notes = []
    for i in range(stop):
        ln = lines[i]
        if not _FRONT_CMD_LINE.match(ln):
            continue
        code = _COMMENT_STRIP.sub("", ln)
        depth = 0
        k = 0
        while k < len(code):
            if code[k] == "\\":
                k += 2
                continue
            depth += 1 if code[k] == "{" else -1 if code[k] == "}" else 0
            k += 1
        nxt = next((x.strip() for x in lines[i + 1:stop] if x.strip()), "")
        if depth > 0 and (not nxt or nxt.startswith("\\")):
            cm = re.search(r"(?<!\\)%", ln)
            at = cm.start() if cm else len(ln)
            lines[i] = ln[:at].rstrip() + "}" * depth + ln[at:]
            name = re.match(r"^[ \t]*(\\[A-Za-z]+(?:\[[^\]]*\])?)", ln).group(1)
            notes.append(f"line {i + 1}: {name}{{...}} is missing its closing brace - closed at "
                         "the end of the line in the built copy; please fix the text")
    return "\n".join(lines), notes


def _move_authblk_first(source: str) -> str:
    r"""``\usepackage{authblk}`` after the first ``\author[..]`` (or any front matter) is too
    late -- move it right behind ``\documentclass``."""
    m = re.search(r"^[ \t]*\\usepackage(?:\[[^\]]*\])?\{authblk\}[ \t]*\n?", source, re.M)
    a = re.search(r"\\author\s*\[", source)
    c = re.search(r"\\documentclass(?:\[[^\]]*\])?\{[^}]*\}[ \t]*\n?", source)
    if m and a and c and m.start() > a.start() and c.end() <= a.start():
        text = source[:m.start()] + source[m.end():]
        return text[:c.end()] + "\\usepackage{authblk}\n" + text[c.end():]
    return source


def normalize_authors(source: str):
    """Build-copy repairs of the front matter: unclosed braces, then :func:`_authors_core`."""
    source, notes = _close_front_braces(source)
    source = _move_authblk_first(source)
    out, more = _authors_core(source)
    return out, notes + more


def _authors_core(source: str):
    r"""Build-copy fixes for author lists: ``authblk`` together with a class that has its own
    author handling (achemso, elsarticle) is converted to that class's syntax; ``\affiliation``
    next to authblk becomes ``\affil``; ``\author[1]{..}`` without authblk gets it.
    Returns ``(source, notes)``; the user's text is never changed."""
    code = _COMMENT_STRIP.sub("", source)
    tid = detect(source)
    has_ab = bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\bauthblk\b", code))
    notes = []
    if has_ab and tid in ("acs", "elsevier"):
        info = parse_authors(source)
        if not info["authors"]:
            return source, notes
        block = render_author_block(tid, info["authors"], info["affiliations"])
        text = re.sub(r"^[ \t]*\\usepackage(?:\[[^\]]*\])?\{authblk\}[ \t]*\n?", "", source,
                      flags=re.M)
        front_end = len(_front_text(text))
        head, rest = text[:front_end], text[front_end:]
        head, _ = _take_commands(head, list(_AUTHOR_CMDS))
        key = "@ACSAUTHORS@" if tid == "acs" else "@ELSAUTHORS@"
        m = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*(?=\{)", head)
        if m and _balanced(head, m.end()):
            at = _balanced(head, m.end())[1]
        else:
            b = re.search(r"\\begin\{document\}", head)
            at = b.start() if b else len(head)
        head = head[:at] + "\n" + block[key] + "\n" + head[at:]
        notes.append("authblk does not work with this class: the author list was converted to "
                     "the class's own author / affiliation commands in the built copy")
        return head + rest, notes
    if has_ab and re.search(r"\\affiliation\b", code) and not re.search(r"\\affil\b", code):
        notes.append(r"\affiliation is not an authblk command: used as \affil in the built copy")
        return re.sub(r"\\affiliation(?=\s*[\[{])", lambda m: r"\affil", source), notes
    if tid == "article" and not has_ab and re.search(r"\\author\s*\[", code):
        c = re.search(r"\\documentclass(?:\[[^\]]*\])?\{[^}]*\}[ \t]*\n?", source)
        if c:           # authblk must be loaded BEFORE the first \author[..]
            notes.append(r"\author[..] needs authblk: added \usepackage{authblk} to the built copy")
            src = source[:c.end()] + "\\usepackage{authblk}\n" + source[c.end():]
            if re.search(r"\\affiliation\b", code) and not re.search(r"\\affil\b", code):
                src = re.sub(r"\\affiliation(?=\s*[\[{])", lambda m: r"\affil", src)
            return src, notes
    return source, notes


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
                   body: str = "", extra=None, affiliations=None) -> str:
    t = get(template_id)
    tid = base_of(template_id)
    blk = render_author_block(tid, list(authors or []), affiliations)
    extra = list(extra or [])
    for line in blk.pop("_extra", []):
        if line not in extra and not any("authblk" in e for e in extra):
            extra.append(line)
    extra_txt = ("\n" + "\n".join(extra)) if extra else ""
    pre = t["preamble"].replace("@EXTRA@", extra_txt.lstrip("\n"))
    front = t["front"].replace("@TITLE@", title or "Title")
    front = front.replace("@ABSTRACT@", abstract or "Abstract.")
    if "@AUTHORBLOCK@" not in t["front"]:       # older saved layouts: \author{@AUTHORS@}
        blk["@AUTHORS@"] = blk["@AUTHORS@"] if tid != "article" else \
            r" \and ".join(n["name"] for n in _norm_authors(authors)[0]) or "Author Name"
    for k, v in blk.items():
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


def apply_template(source: str, template_id: str, authors=None, affiliations=None) -> str:
    """The document's text in another layout: preamble and front matter are regenerated from
    title / authors / abstract, the body (text, figures, bibliography) is kept."""
    if template_id not in TEMPLATES and not (is_user(template_id)
                                             and template_id in load_user_templates()):
        return source
    parts = extract_parts(source)
    body = _fix_envs_for(base_of(template_id), parts["body"])
    if authors is None:
        authors = parts["author_info"] or parts["authors"]
        affiliations = parts["affiliations"] if affiliations is None else affiliations
    return build_document(template_id, parts["title"] or "Title", authors,
                          parts["abstract"], body, parts["extra"], affiliations)


def compose(source: str, template_id: str, title: str = "", authors=None,
            abstract: str = "", affiliations=None) -> str:
    """For ``push_document(template=...)``: a complete source text in ``template_id`` from
    either a full document (switched) or just body text (wrapped)."""
    if r"\documentclass" in source:
        return apply_template(source, template_id, authors, affiliations)
    return build_document(template_id, title or "Title", authors, abstract, source,
                          None, affiliations)


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


# ----------------------------------------------------------- Supporting Information
SI_COUNTERS = [r"\renewcommand{\thefigure}{S\arabic{figure}}",
               r"\renewcommand{\thetable}{S\arabic{table}}",
               r"\renewcommand{\theequation}{S\arabic{equation}}",
               r"\renewcommand{\thepage}{S\arabic{page}}"]
SI_BODY = (r"\section*{Supporting Information}" "\n"
           "This document contains additional data and figures supporting the main text.\n\n"
           r"\section{Additional data}" "\n\n" r"\section{Additional figures}" "\n\n")


def si_document(source: str, title: str | None = None) -> str:
    r"""A Supporting-Information document derived from ``source`` (the main paper): same class,
    preamble, macros, title block and authors, a new title / abstract, an empty body skeleton
    and the counters of figures, tables, equations and pages prefixed with ``S``
    (``Figure S1``, ``(S1)``). References and figures are project-wide, so the SI can cite
    and show the same items."""
    m = re.search(r"\\begin\{document\}", source)
    if not m:
        raise ValueError("the document has no \\begin{document}")
    text = source
    # title
    t = re.search(r"\\title\s*(\[[^\]]*\])?\s*(?=\{)", text)
    if t:
        g = _balanced(text, t.end())
        if g:
            new = title or "Supporting Information for: " + " ".join(g[0].split())
            text = text[:t.end()] + "{" + new + "}" + text[g[1]:]
    # abstract
    text = re.sub(_ENV.format(n="abstract"),
                  lambda mm: "\\begin{abstract}\nSupporting information for the main text.\n"
                             "\\end{abstract}", text, count=1, flags=re.S)
    b = re.search(r"\\begin\{document\}", text)
    cuts = [b.end()]
    for pat in (r"\\end\{abstract\}", r"\\end\{frontmatter\}", r"\\maketitle",
                r"\\end\{@twocolumnfalse\}\s*\]"):
        mm = re.search(pat, text[b.end():])
        if mm:
            cuts.append(b.end() + mm.end())
    start = max(cuts)
    ends = list(re.finditer(r"\\end\{document\}", text))
    end = ends[-1].start() if ends else len(text)
    text = text[:start] + "\n\n" + SI_BODY + text[end:]
    if not ends:
        text += "\\end{document}\n"
    b = re.search(r"\\begin\{document\}", text)
    have = set(re.findall(r"\\renewcommand\{\\the(figure|table|equation|page)\}", text[:b.start()]))
    add = [c for c in SI_COUNTERS
           if re.search(r"\\the(\w+)\}", c).group(1) not in have]
    if add:
        text = text[:b.start()] + "\n".join(add) + "\n" + text[b.start():]
    return text
