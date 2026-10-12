"""Zotero without a Better BibTeX *export*: pick items in Zotero, store them in the project,
write ``references.bib`` ourselves.

Items are picked through Zotero's "cite as you write" popup -- the endpoint
``http://127.0.0.1:23119/better-bibtex/cayw?format=json`` (the same one the Obsidian
``Zoterolink`` script uses; it needs Zotero running with the Better BibTeX plugin installed, but
*not* a working library export). The returned items are normalized to a small record (accepting
CSL-JSON and Zotero-JSON shapes), kept in ``ProjectModel.references`` and turned into BibTeX by
:func:`to_bibtex` -- so documents compile and cite offline afterwards.

Stdlib only.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:23119"
CAYW_URL = BASE + "/better-bibtex/cayw?format=json"


class ZoteroError(RuntimeError):
    pass


# ------------------------------------------------------------------------ talking to Zotero
def ping(timeout: float = 1.5, base: str = BASE) -> bool:
    """Is Zotero running with its local connector server?"""
    try:
        with urllib.request.urlopen(base + "/connector/ping", timeout=timeout) as r:
            return r.status == 200
    except (OSError, urllib.error.URLError):
        return False


def cayw_pick(timeout: float = 900.0, url: str = CAYW_URL) -> list:
    """Open Zotero's picker and block until the user confirms; returns the raw items
    (``[]`` if cancelled). The popup appears *in Zotero*."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        raise ZoteroError(f"Zotero answered HTTP {exc.code} - is the Better BibTeX plugin "
                          "installed and enabled?") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ZoteroError("Zotero is not reachable on 127.0.0.1:23119 - start Zotero "
                          "(with Better BibTeX) first.") from exc
    if not body or body == "[]":
        return []
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ZoteroError(f"unexpected answer from Zotero: {body[:200]!r}") from exc
    return data if isinstance(data, list) else [data]


# --------------------------------------------------------------------------- normalizing
_ITEM_URI = re.compile(r"/items/([A-Z0-9]{8})\b")


def _first(d: dict, *names, default=""):
    for n in names:
        v = d.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def _people(raw: dict, kind: str) -> list:
    """[(family, given)] for ``kind`` ("author" | "editor"), CSL or Zotero shaped.
    A single-field name (organisation) is returned as ``(name, "")`` with a trailing ``*``
    marker on the family so BibTeX output can brace it."""
    out = []
    csl = raw.get(kind)
    if isinstance(csl, list):
        for p in csl:
            if isinstance(p, dict):
                fam, giv = p.get("family", ""), p.get("given", "")
                if not fam and p.get("literal"):
                    out.append((p["literal"] + "*", ""))
                elif fam or giv:
                    out.append((fam, giv))
    for p in raw.get("creators") or []:
        if not isinstance(p, dict):
            continue
        if (p.get("creatorType") or "author") != kind:
            continue
        fam, giv = p.get("lastName", ""), p.get("firstName", "")
        if not fam and p.get("name"):
            out.append((p["name"] + "*", ""))
        elif fam or giv:
            out.append((fam, giv))
    return out


def _year_month(raw: dict):
    issued = raw.get("issued")
    if isinstance(issued, dict):
        parts = (issued.get("date-parts") or [[]])[0]
        if parts:
            return str(parts[0]), (str(parts[1]) if len(parts) > 1 else "")
        text = issued.get("raw") or issued.get("literal") or ""
    else:
        text = _first(raw, "date", "year", default="")
    m = re.search(r"(1[5-9]\d\d|20\d\d|21\d\d)", str(text))
    return (m.group(1) if m else ""), ""


def zotero_item_key(raw: dict) -> str:
    """The 8-character Zotero item key, from whichever field carries it."""
    for name in ("itemKey", "item-key", "zoteroKey", "key"):
        v = raw.get(name)
        if isinstance(v, str) and re.fullmatch(r"[A-Z0-9]{8}", v):
            return v
    for name in ("zotero-key", "uri", "id", "zotero", "select", "itemID"):
        v = raw.get(name)
        if isinstance(v, str):
            m = _ITEM_URI.search(v) or re.search(r"\b([A-Z0-9]{8})\b$", v)
            if m:
                return m.group(1)
    return ""


def normalize(raw: dict) -> dict | None:
    """Canonical reference record from a Zotero/BBT/CSL item (``None`` if unusable)."""
    if not isinstance(raw, dict):
        return None
    title = _first(raw, "title")
    key = _first(raw, "citationKey", "citation-key", "citekey", "citation_key", default="")
    if not key and isinstance(raw.get("id"), str) and not raw["id"].isdigit() \
            and "/" not in raw["id"] and len(raw["id"]) != 8:
        key = raw["id"]
    year, month = _year_month(raw)
    rec = {
        "key": str(key),
        "type": str(_first(raw, "type", "itemType", default="misc")),
        "title": str(title),
        "authors": _people(raw, "author"),
        "editors": _people(raw, "editor"),
        "year": year,
        "month": month,
        "container": str(_first(raw, "container-title", "publicationTitle", "bookTitle",
                                "proceedingsTitle", "websiteTitle", default="")),
        "volume": str(_first(raw, "volume", default="")),
        "issue": str(_first(raw, "issue", "number", default="")),
        "pages": str(_first(raw, "page", "pages", default="")),
        "doi": str(_first(raw, "DOI", "doi", default="")),
        "url": str(_first(raw, "URL", "url", default="")),
        "publisher": str(_first(raw, "publisher", "institution", "university", default="")),
        "place": str(_first(raw, "publisher-place", "place", default="")),
        "isbn": str(_first(raw, "ISBN", "isbn", default="")),
        "zotero_key": zotero_item_key(raw),
    }
    if not (rec["title"] or rec["authors"]):
        return None
    rec["authors"] = [list(p) for p in rec["authors"]]
    rec["editors"] = [list(p) for p in rec["editors"]]
    if not rec["key"]:
        rec["key"] = make_key(rec)
    rec["key"] = re.sub(r"[^A-Za-z0-9_:.+-]", "", rec["key"]) or make_key(rec)
    return rec


def make_key(rec: dict) -> str:
    """``smith2020quantum``-style key when Zotero supplies none."""
    fam = (rec.get("authors") or rec.get("editors") or [["anon", ""]])[0][0].rstrip("*")
    fam = re.sub(r"[^A-Za-z]", "", fam.split()[-1] if fam.split() else fam).lower() or "anon"
    word = next((w for w in re.findall(r"[A-Za-z]{4,}", rec.get("title", ""))
                 if w.lower() not in {"with", "from", "that", "this", "their", "using"}), "")
    return f"{fam}{rec.get('year', '')}{word.lower()}"


# ------------------------------------------------------------------------------ BibTeX
_TYPE_MAP = {
    "journalarticle": "article", "article-journal": "article", "article": "article",
    "magazinearticle": "article", "article-magazine": "article",
    "book": "book",
    "booksection": "incollection", "chapter": "incollection",
    "conferencepaper": "inproceedings", "paper-conference": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
}


def _esc(text: str) -> str:
    out = str(text)
    for ch in "&%#_":
        out = out.replace(ch, "\\" + ch)
    return out.replace("~", "\\textasciitilde{}")


def _names(people) -> str:
    out = []
    for fam, giv in people:
        if fam.endswith("*"):
            out.append("{" + _esc(fam[:-1]) + "}")
        else:
            out.append(f"{_esc(fam)}, {_esc(giv)}" if giv else _esc(fam))
    return " and ".join(out)


def _pages(p: str) -> str:
    return re.sub(r"(?<=\d)\s*[-\u2013\u2014]+\s*(?=\d)", "--", p)


def to_bibtex(rec: dict) -> str:
    """One BibTeX entry for a normalized record."""
    typ = _TYPE_MAP.get(str(rec.get("type", "")).lower(), "misc")
    fields = []

    def add(name, value, brace=False):
        if value:
            v = _esc(value)
            fields.append((name, "{" + v + "}" if brace else v))

    if rec.get("authors"):
        fields.append(("author", _names(rec["authors"])))
    if rec.get("editors"):
        fields.append(("editor", _names(rec["editors"])))
    add("title", rec.get("title"), brace=True)       # {{...}}: keep the capitalization
    container = rec.get("container", "")
    if typ == "article":
        add("journal", container)
    elif typ in ("incollection", "inproceedings"):
        add("booktitle", container)
    elif container:
        add("howpublished", container)
    add("volume", rec.get("volume"))
    add("number", rec.get("issue"))
    if rec.get("pages"):
        fields.append(("pages", _pages(_esc(rec["pages"]))))
    add("year", rec.get("year"))
    if typ == "phdthesis":
        add("school", rec.get("publisher"))
    elif typ == "techreport":
        add("institution", rec.get("publisher"))
    else:
        add("publisher", rec.get("publisher"))
    add("address", rec.get("place"))
    add("isbn", rec.get("isbn"))
    if rec.get("doi"):
        fields.append(("doi", rec["doi"]))
    if rec.get("url"):
        fields.append(("url", rec["url"]))
    body = ",\n".join(f"  {n} = {{{v}}}" for n, v in fields)
    return f"@{typ}{{{rec['key']},\n{body}\n}}\n"


_CITE_RE = re.compile(r"\\(?:[A-Za-z]*cite[A-Za-z]*|nocite)\*?\s*(?:\[[^\]]*\]\s*)*\{([^{}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*")


def cite_keys(source: str) -> list:
    """Every citation key used in ``source`` (``\\cite``, ``\\citep``, ``\\citet``, ...)."""
    seen, out = set(), []
    for line in source.splitlines():
        for m in _CITE_RE.finditer(_COMMENT_RE.sub("", line)):
            for k in m.group(1).split(","):
                k = k.strip()
                if k and k != "*" and k not in seen:
                    seen.add(k)
                    out.append(k)
    return out


def bibtex_file(records, used=None) -> str:
    """``references.bib`` text: every stored record (or only those whose key is in ``used``)."""
    keep = set(used) if used is not None else None
    return "\n".join(to_bibtex(r) for r in records if keep is None or r["key"] in keep)


def zotero_url(rec: dict) -> str:
    """``zotero://select/library/items/KEY`` -- opens the item in the Zotero app."""
    return f"zotero://select/library/items/{rec['zotero_key']}" if rec.get("zotero_key") else ""


def label(rec: dict) -> str:
    """Short human label for lists: ``Smith et al. (2020) - Title``."""
    a = rec.get("authors") or rec.get("editors") or []
    who = a[0][0].rstrip("*") if a else "n.a."
    if len(a) > 1:
        who += " et al."
    return f"{who} ({rec.get('year') or 's.d.'}) - {rec.get('title') or '(untitled)'}"


# --------------------------------------------------------------- optional local API search
def local_search(query: str, limit: int = 30, base: str = BASE) -> list:
    """Search the library through Zotero 7's *local API* (CSL-JSON). That API is off by
    default (Zotero: Settings -> Advanced -> "Allow other applications on this computer to
    communicate with Zotero"; plus ``extensions.zotero.httpServer.localAPI.enabled``); a 403
    raises :class:`ZoteroError` with that hint."""
    q = urllib.parse.urlencode({"q": query, "limit": limit, "format": "csljson",
                                "itemType": "-attachment || note"})
    try:
        with urllib.request.urlopen(f"{base}/api/users/0/items?{q}", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise ZoteroError("Zotero's local API is disabled (HTTP 403). Enable it: Zotero "
                              "Settings -> Advanced -> allow other applications to "
                              "communicate with Zotero, and set "
                              "extensions.zotero.httpServer.localAPI.enabled = true in the "
                              "Config Editor.") from exc
        raise ZoteroError(f"Zotero answered HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ZoteroError("Zotero is not reachable on 127.0.0.1:23119.") from exc
    items = data.get("items", data) if isinstance(data, dict) else data
    return [r for r in (normalize(i) for i in items) if r]
