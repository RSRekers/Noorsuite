r"""Native reference formatting: citations numbered by first appearance + an ACS-style list.

BibTeX styles (``achemso.bst``) are years behind the ACS Guide and cannot do hyperlinked DOIs or
the relaxed 2025 rules, so for the default format NoorSuite writes the list itself, at build time,
into the *built copy* of the document (the user's text keeps ``\cite{key}``):

* every ``\cite`` / ``\citep`` / ``\citet`` becomes a superscript number, numbered in order of first
  appearance (``\textsuperscript{1,2}``, consecutive runs of three or more as ``1--3``);
* the ``\bibliography{...}`` marker (or the end of the document) gets a ``thebibliography`` with
  the cited records only, in that order, labelled ``(1)``, ``(2)`` ...

Rules implemented (ACS Guide to Scholarly Communication, chapter 4.3 "References", revised April
2025 -- no 2026 revision was found; every rule lives in this file so it is easy to adjust):

* authors ``Family, A. B.; Family, C.`` (all authors unless ``max_authors`` is set);
* journal article: ``Authors. Title. *Journal* **Year**, *Volume* (Issue), Pages. DOI: ...``;
  journal names abbreviated *or* complete -- but consistently across the list;
* the DOI is a hyperlink (``https://doi.org/...``); no access dates for URLs;
* books: ``Authors. *Title*; Publisher: Place, Year.``; chapters ``In *Book*; Editors, Ed.; ...``.

Qt-free, stdlib only.
"""
from __future__ import annotations

import re

from . import zotero

_CITE_RE = re.compile(
    r"\\(cite|citep|citet|citealp|citeauthor|citeyear)\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^{}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_SPECIAL = {"&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_"}


def esc(text: str) -> str:
    """Escape LaTeX specials in bibliographic text (``$...$`` math is left alone)."""
    out, in_math = [], False
    for ch in str(text):
        if ch == "$":
            in_math = not in_math
            out.append(ch)
        elif ch in _SPECIAL and not in_math:
            out.append(_SPECIAL[ch])
        else:
            out.append(ch)
    return "".join(out).replace("~", r"\textasciitilde{}")


def initials(given: str) -> str:
    """``Jane Ann`` -> ``J. A.``; ``Jean-Pierre`` -> ``J.-P.``; ``J.A.`` -> ``J. A.``"""
    out = []
    for token in re.split(r"[\s]+", given.strip()):
        if not token:
            continue
        parts = re.split(r"(-)", token)
        piece = ""
        for p in parts:
            if p == "-":
                piece += "-"
            else:
                letters = re.findall(r"[^\W\d_]\.?", p) if "." in p and len(p) <= 4 \
                    else [p[0]] if p else []
                piece += " ".join(l.rstrip(".") + "." for l in letters)
        out.append(piece)
    return " ".join(out)


def authors_text(people, max_authors=None) -> str:
    names = []
    for fam, giv in people:
        if fam.endswith("*"):
            names.append(esc(fam[:-1]))
        else:
            ini = initials(giv)
            names.append(f"{esc(fam)}, {ini}" if ini else esc(fam))
    if max_authors and len(names) > max_authors:
        names = names[:max_authors] + ["et al."]
    return "; ".join(names)


def _pages(p: str) -> str:
    return re.sub(r"(?<=\w)\s*[-\u2013\u2014]+\s*(?=\w)", "--", esc(p))


def _doi(rec) -> str:
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", rec.get("doi", "").strip(), flags=re.I)
    if not doi:
        return ""
    return rf"\href{{https://doi.org/{esc(doi)}}}{{DOI: {esc(doi)}}}"


def _join(parts, sep=" "):
    return sep.join(p for p in parts if p)


def format_reference(rec: dict, abbreviate: bool = False, max_authors=None) -> str:
    """One bibliography entry (LaTeX) for a normalized record, ACS Guide layout."""
    typ = str(rec.get("type", "")).lower()
    who = authors_text(rec.get("authors") or [], max_authors)
    editors = authors_text(rec.get("editors") or [], max_authors)
    title = esc(rec.get("title", "").rstrip("."))
    year = rec.get("year", "")
    container = esc((rec.get("container_short") if abbreviate and rec.get("container_short")
                     else rec.get("container", "")))
    vol, issue, pages = esc(rec.get("volume", "")), esc(rec.get("issue", "")), _pages(
        rec.get("pages", ""))
    doi = _doi(rec)
    url = rec.get("url", "").strip()
    link = doi or (rf"\url{{{url}}}" if url else "")

    def lead():
        if not who:
            return ""
        return who + (" " if who.endswith(".") else ". ")      # initials already end in "."

    if typ in ("journalarticle", "article-journal", "article", "magazinearticle",
               "article-magazine", "article-newspaper"):
        src = _join([rf"\textit{{{container}}}" if container else "",
                     rf"\textbf{{{year}}}," if year and (vol or pages) else
                     (rf"\textbf{{{year}}}" if year else ""),
                     (rf"\textit{{{vol}}}" + (f" ({issue})" if issue else "")
                      + ("," if pages else "")) if vol else (f"({issue})," if issue else ""),
                     pages])
        body = f"{lead()}{title}. {src.rstrip(',')}."
        return _join([body, (link + "." if False else link)])
    if typ in ("book", "edited-book"):
        pub = _join([esc(rec.get("publisher", "")) + (":" if rec.get("place") else ""),
                     esc(rec.get("place", ""))])
        tail = _join([pub.rstrip(":") + ("," if year and pub else ""), year])
        ed = f"{editors}, Ed{'s' if ';' in editors else ''}.; " if editors and not who else ""
        who_part = lead() if who else (f"{editors}, Ed{'s' if ';' in editors else ''}. "
                                       if editors else "")
        body = f"{who_part}\\textit{{{title}}}; {ed if who else ''}{tail}."
        return _join([body.replace("; .", "."), link])
    if typ in ("booksection", "chapter", "inbook", "incollection"):
        pub = _join([esc(rec.get("publisher", "")) + (":" if rec.get("place") else ""),
                     esc(rec.get("place", ""))])
        ed = f"{editors}, Ed{'s' if ';' in editors else ''}.; " if editors else ""
        tail = _join([pub.rstrip(":") + ("," if year and pub else ""), year])
        pg = f"; pp {pages}" if pages else ""
        body = f"{lead()}{title}. In \\textit{{{container}}}; {ed}{tail}{pg}."
        return _join([body, link])
    if typ in ("thesis", "phdthesis", "mastersthesis"):
        inst = _join([esc(rec.get("publisher", "")), esc(rec.get("place", ""))], ", ")
        body = f"{lead()}{title}. Ph.D. Dissertation, {_join([inst, year], ', ')}."
        return _join([body.replace("Dissertation, .", "Dissertation."), link])
    if typ in ("conferencepaper", "paper-conference", "inproceedings"):
        body = _join([f"{lead()}{title}.", rf"\textit{{{container}}};" if container else "",
                      _join([esc(rec.get("publisher", "")), esc(rec.get("place", "")),
                             year], ", ") + ("." if (rec.get("publisher") or rec.get("place")
                                                      or year) else ""),
                      f"pp {pages}." if pages else ""])
        return _join([body, link])
    # reports, preprints, datasets, software, web pages, anything else
    pub = _join([esc(rec.get("publisher", "")), container,
                 esc(rec.get("place", ""))], ", ")
    tail = _join([pub, year], ", ")
    body = f"{lead()}{title}. {tail}." if tail else f"{lead()}{title}."
    return _join([body.replace(" .", "."), link])


# -------------------------------------------------------------------------- numbering
def _range_text(nums) -> str:
    nums = sorted(set(nums))
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            out.append(f"{nums[i]}--{nums[j]}")
        else:
            out += [str(n) for n in nums[i:j + 1]]
        i = j + 1
    return ",".join(out)


CITE_MODES = {"superscript": "Superscript numbers",
              "parentheses": "Italic numbers in parentheses (1)",
              "brackets": "Numbers in square brackets [1]"}


def _wrap_numbers(text: str, mode: str) -> str:
    if mode == "parentheses":
        return rf"(\textit{{{text}}})"
    if mode == "brackets":
        return f"[{text}]"
    return rf"\textsuperscript{{{text}}}"


def number_citations(source: str, records, max_authors=None, abbreviate=None,
                     cite_mode: str = "superscript"):
    r"""``(new_source, cited_keys, undefined, notes)``.

    ``\cite``-family commands become superscript numbers by order of first appearance, the
    ``\bibliography{...}`` marker (or the end of the document) becomes the formatted list.
    ``abbreviate=None`` abbreviates journal names only if *every* cited journal record has an
    abbreviation (the Guide wants one convention for the whole list)."""
    by_key = {r["key"]: r for r in records}
    order: list = []
    undefined: list = []

    def num_of(key):
        if key not in order:
            order.append(key)
        return order.index(key) + 1

    lines_out = []
    for line in source.splitlines(keepends=True):
        code, comment = line, ""
        m = re.search(r"(?<!\\)%", line)
        if m:
            code, comment = line[:m.start()], line[m.start():]

        def repl(mm):
            cmd, keys_text = mm.group(1), mm.group(2)
            keys = [k.strip() for k in keys_text.split(",") if k.strip()]
            nums = []
            for k in keys:
                if k not in by_key:
                    if k not in undefined:
                        undefined.append(k)
                    continue
                nums.append(num_of(k))
            if cmd == "citeauthor":
                rec = by_key.get(keys[0]) if keys else None
                if rec and rec.get("authors"):
                    fam = rec["authors"][0][0].rstrip("*")
                    return esc(fam) + (" et al." if len(rec["authors"]) > 1 else "")
                return "?"
            if cmd == "citeyear":
                rec = by_key.get(keys[0]) if keys else None
                return esc(rec.get("year", "")) if rec else "?"
            sup = _wrap_numbers(_range_text(nums), cite_mode) if nums \
                else _wrap_numbers("?", cite_mode)
            if cmd == "citet" and keys and keys[0] in by_key and by_key[keys[0]].get("authors"):
                fam = by_key[keys[0]]["authors"][0][0].rstrip("*")
                return esc(fam) + (" et al." if len(by_key[keys[0]]["authors"]) > 1 else "") + sup
            return sup

        lines_out.append(_CITE_RE.sub(repl, code) + comment)
    text = "".join(lines_out)

    cited = [by_key[k] for k in order]
    notes = []
    if abbreviate is None:
        journals = [r for r in cited if r.get("container")
                    and str(r.get("type", "")).lower() in
                    ("journalarticle", "article-journal", "article")]
        abbreviate = bool(journals) and all(r.get("container_short") for r in journals)
    entries = "\n".join(
        rf"\bibitem{{{r['key']}}} {format_reference(r, abbreviate, max_authors)}" for r in cited)
    block = (r"\makeatletter\renewcommand\@biblabel[1]{(#1)}\makeatother" "\n"
             rf"\begin{{thebibliography}}{{{max(9, len(cited))}}}" "\n" + entries +
             "\n" r"\end{thebibliography}" "\n")
    if not order:
        return source, [], undefined, notes
    marker = re.compile(r"^[ \t]*\\bibliography\s*\{[^}]*\}[ \t]*\n?", re.M)
    style_line = re.compile(r"^[ \t]*\\bibliographystyle\s*\{[^}]*\}[ \t]*\n?", re.M)
    text = style_line.sub("", text)
    if marker.search(text):
        text = marker.sub(lambda _m: block, text, count=1)
        text = marker.sub("", text)
    else:
        ends = list(re.finditer(r"\\end\{document\}", text))
        if ends:
            at = ends[-1].start()
            text = text[:at] + block + text[at:]
            notes.append("no reference list in the text: the ACS list was added before "
                         r"\end{document} of the built copy - use References -> Insert "
                         "reference list to place it yourself")
    return text, [r["key"] for r in cited], undefined, notes
