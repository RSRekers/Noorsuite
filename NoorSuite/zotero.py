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

    def val(*names):
        v = str(_first(raw, *names, default="")).replace("\u00a0", " ").strip()
        return "" if v.lower() in ("n/a", "na", "none", "null", "-", "0") else v

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
        "container_short": str(_first(raw, "container-title-short", "journalAbbreviation",
                                      "journal-abbreviation", default="")),
        "volume": val("volume"),
        "issue": val("issue", "number"),
        "pages": val("page", "pages"),
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


# ------------------------------------------------------------------- library import
def merge_record(existing: dict, new: dict) -> dict:
    """``new`` data under ``existing``'s cite key (an item picked earlier keeps the key the
    text already cites); a generated key is replaced by Zotero's own when ``new`` has one."""
    out = {**existing, **{k: v for k, v in new.items() if v not in ("", [], None)}}
    generated = existing["key"] == make_key(existing)
    out["key"] = new["key"] if generated and new["key"] != make_key(new) else existing["key"]
    return out


def local_collections(base: str = BASE) -> list:
    """[(key, name)] of the library's collections via the local API (see ``local_search``)."""
    try:
        with urllib.request.urlopen(f"{base}/api/users/0/collections?limit=200",
                                    timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ZoteroError(_LOCAL_API_HINT if exc.code == 403 else f"HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ZoteroError("Zotero is not reachable on 127.0.0.1:23119.") from exc
    return [(c.get("key") or c["data"]["key"], c.get("data", c).get("name", ""))
            for c in data if isinstance(c, dict)]


def local_library(collection: str | None = None, base: str = BASE, page: int = 100) -> list:
    """Every top-level item of the library (or of one collection) as normalized records,
    through Zotero's local API. Raises :class:`ZoteroError` with how to enable it."""
    path = f"/api/users/0/collections/{collection}/items/top" if collection \
        else "/api/users/0/items/top"
    out, start = [], 0
    while True:
        q = urllib.parse.urlencode({"format": "csljson", "limit": page, "start": start})
        try:
            with urllib.request.urlopen(f"{base}{path}?{q}", timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ZoteroError(_LOCAL_API_HINT if exc.code == 403 else f"HTTP {exc.code}") from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ZoteroError("Zotero is not reachable on 127.0.0.1:23119.") from exc
        items = data.get("items", data) if isinstance(data, dict) else data
        recs = [r for r in (normalize(i) for i in items) if r]
        out += recs
        if len(items) < page or start > 20000:
            return out
        start += page


_LOCAL_API_HINT = ("Zotero's local API is switched off (HTTP 403). Either enable it -- Zotero "
                   "Settings -> Advanced -> allow other applications on this computer to "
                   "communicate with Zotero, and in the Config Editor set "
                   "extensions.zotero.httpServer.localAPI.enabled = true -- or use Zotero's own "
                   "File -> Export Library... (BibTeX or CSL JSON) and import that file here.")


def _split_braced(text: str, i: int):
    """text[i] is '{' -> (content, index after the matching '}')."""
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
    return text[i + 1:], len(text)


_ACCENTS = {
    '"': dict(zip("aouAOUe", "\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00eb")),
    "'": dict(zip("aeiouAEOUy", "\u00e1\u00e9\u00ed\u00f3\u00fa\u00c1\u00c9\u00d3\u00da\u00fd")),
    "`": dict(zip("aeiouAE", "\u00e0\u00e8\u00ec\u00f2\u00f9\u00c0\u00c8")),
    "^": dict(zip("aeiou", "\u00e2\u00ea\u00ee\u00f4\u00fb")),
    "~": {"n": "\u00f1", "N": "\u00d1", "a": "\u00e3", "o": "\u00f5"},
}


def _untex(value: str) -> str:
    """BibTeX/LaTeX text -> plain unicode: common accents, escaped specials, braces."""
    def accent(m):
        return _ACCENTS.get(m.group(1), {}).get(m.group(2), m.group(2))
    out = re.sub(r"\\([\"'`^~])\s*\{?\\?([A-Za-z])\}?", accent, value)
    out = out.replace("\\ss", "\u00df").replace("{\\ss}", "\u00df")
    out = re.sub(r"\\c\s*\{?c\}?", "\u00e7", out)
    for esc in ("&", "%", "_", "#", "$"):
        out = out.replace("\\" + esc, esc)
    out = re.sub(r"[{}]", "", out)
    return re.sub(r"\s+", " ", out).strip()


def parse_bibtex(text: str) -> list:
    """Normalized records from BibTeX text (Zotero's own export, Better BibTeX, any .bib)."""
    records = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        typ, key = m.group(1).lower(), m.group(2)
        if typ in ("comment", "string", "preamble"):
            continue
        i, fields = m.end(), {}
        while i < len(text):
            fm = re.compile(r"\s*([A-Za-z][\w:-]*)\s*=\s*").match(text, i)
            if fm is None:
                break
            name, i = fm.group(1).lower(), fm.end()
            if i < len(text) and text[i] == "{":
                val, i = _split_braced(text, i)
            elif i < len(text) and text[i] == '"':
                j = i + 1
                while j < len(text) and not (text[j] == '"' and text[j - 1] != "\\"):
                    j += 1
                val, i = text[i + 1:j], j + 1
            else:
                vm = re.compile(r"[^,}\s]+").match(text, i)
                val, i = (vm.group(0), vm.end()) if vm else ("", i)
            fields[name] = val
            cm = re.compile(r"\s*,").match(text, i)
            i = cm.end() if cm else i
            if re.compile(r"\s*\}").match(text, i):
                break

        def people(s):
            out = []
            for p in re.split(r"\s+and\s+", s or ""):
                p = p.strip()
                if not p:
                    continue
                if p.startswith("{") and p.endswith("}"):
                    out.append([_untex(p) + "*", ""])
                elif "," in p:
                    fam, giv = p.split(",", 1)
                    out.append([_untex(fam), _untex(giv)])
                else:
                    bits = p.split()
                    out.append([_untex(bits[-1]), _untex(" ".join(bits[:-1]))])
            return out

        rec = {
            "key": key,
            "type": {"article": "article-journal", "inproceedings": "paper-conference",
                     "conference": "paper-conference", "incollection": "chapter",
                     "inbook": "chapter", "phdthesis": "thesis", "mastersthesis": "thesis",
                     "techreport": "report"}.get(typ, typ),
            "title": _untex(fields.get("title", "")),
            "authors": people(fields.get("author")),
            "editors": people(fields.get("editor")),
            "year": (re.search(r"\d{4}", fields.get("year", fields.get("date", "")))
                     or [""])[0] if re.search(r"\d{4}", fields.get("year", fields.get("date", "")))
            else "",
            "month": _untex(fields.get("month", "")),
            "container": _untex(fields.get("journal") or fields.get("booktitle")
                                or fields.get("journaltitle") or ""),
            "volume": _untex(fields.get("volume", "")),
            "issue": _untex(fields.get("number") or fields.get("issue") or ""),
            "pages": _untex(fields.get("pages", "")).replace("--", "-"),
            "doi": _untex(fields.get("doi", "")),
            "url": fields.get("url", "").strip(),
            "publisher": _untex(fields.get("publisher") or fields.get("school")
                                or fields.get("institution") or ""),
            "place": _untex(fields.get("address", "")),
            "isbn": _untex(fields.get("isbn", "")),
            "zotero_key": "",
        }
        if rec["title"] or rec["authors"]:
            records.append(rec)
    return records


def load_file(path) -> list:
    """Records from a ``.bib`` (BibTeX) or ``.json`` (CSL-JSON / Zotero JSON) export file."""
    text = open(path, encoding="utf-8", errors="replace").read()
    if str(path).lower().endswith((".json", ".csljson")):
        data = json.loads(text)
        items = data.get("items", data) if isinstance(data, dict) else data
        return [r for r in (normalize(i) for i in items) if r]
    return parse_bibtex(text)


# ----------------------------------------------- Better BibTeX JSON-RPC (no picker, no export)
RPC_URL = BASE + "/better-bibtex/json-rpc"


def rpc(method: str, params=None, timeout: float = 180.0, url: str | None = None):
    """Call a Better BibTeX JSON-RPC method (it ships with the plugin and needs neither the
    picker nor the plugin's library *export*). Raises :class:`ZoteroError`."""
    url = url or RPC_URL
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or [],
                       "id": 1}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ZoteroError(f"Zotero answered HTTP {exc.code} - is Better BibTeX enabled?") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ZoteroError("Zotero is not reachable on 127.0.0.1:23119 - start Zotero "
                          "(with Better BibTeX) first.") from exc
    if out.get("error"):
        raise ZoteroError(f"Better BibTeX: {out['error'].get('message', out['error'])}")
    return out.get("result")


def rpc_available() -> bool:
    try:
        return bool(rpc("api.ready", timeout=2.0))
    except ZoteroError:
        return False


def rpc_search(terms: str = "", limit: int | None = None) -> list:
    """Normalized records of every library item matching ``terms`` (``""`` = the whole
    library, several thousand items in a few seconds). Items without a citation key get a
    generated one; attachments / notes are not returned by Zotero here."""
    items = rpc("item.search", [terms or ""]) or []
    out, seen = [], set()
    for raw in items:
        rec = normalize(raw)
        if rec and rec["key"] not in seen:
            seen.add(rec["key"])
            out.append(rec)
            if limit and len(out) >= limit:
                break
    return out


def rpc_find_keys(keys) -> tuple:
    """``(records, missing)``: look up citation keys (as typed in a document or an
    Obsidian ``[[@key]]``) in the library; only an exact citation-key match counts."""
    found, missing = [], []
    for key in keys:
        try:
            hits = rpc("item.search", [key], timeout=60.0) or []
        except ZoteroError:
            missing.append(key)
            continue
        rec = None
        for raw in hits:
            ck = raw.get("citation-key") or raw.get("citationKey") or raw.get("citekey")
            if ck == key:
                rec = normalize(raw)
                break
        (found if rec else missing).append(rec or key)
    return found, missing


def is_complete(rec: dict) -> bool:
    """A record with something to cite by: authors (or editors) *and* a year. Picker items
    sometimes arrive as little more than a title -- those get completed from the library."""
    return bool(rec.get("authors") or rec.get("editors")) and bool(rec.get("year"))
