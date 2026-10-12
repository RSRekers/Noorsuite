"""Tiny dependency-free fuzzy matcher used by the search boxes.

``fuzzy_match`` is a case-insensitive subsequence test: every character of the query
must appear in the text in order (not necessarily contiguously). ``fuzzy_score`` gives
a rough quality number (higher = better) for ranking.
"""
from __future__ import annotations


def fuzzy_match(query: str, text: str) -> bool:
    query = (query or "").strip().lower()
    if not query:
        return True
    text = (text or "").lower()
    it = iter(text)
    return all(ch in it for ch in query)


def fuzzy_score(query: str, text: str) -> float:
    """0.0 = no match. Rewards contiguity and early / whole-word matches."""
    q = (query or "").strip().lower()
    if not q:
        return 1.0
    t = (text or "").lower()
    if q in t:
        # contiguous hit: best; earlier and word-boundary starts score higher
        start = t.index(q)
        boundary = 1.0 if start == 0 or not t[start - 1].isalnum() else 0.0
        return 3.0 + boundary - start / (len(t) + 1)
    if not fuzzy_match(q, t):
        return 0.0
    # spread of the subsequence match: tighter is better
    idx, first, last, streak, best_streak = 0, None, 0, 0, 0
    prev = -2
    for ch in q:
        idx = t.index(ch, last if first is not None else 0)
        if first is None:
            first = idx
        streak = streak + 1 if idx == prev + 1 else 1
        best_streak = max(best_streak, streak)
        prev = idx
        last = idx + 1
    spread = (prev - first + 1) or 1
    return 1.0 + best_streak / len(q) - (spread - len(q)) / (spread + 1)


# --------------------------------------------------------------------------- smart search
import re as _re

_QUERY_RE = _re.compile(r'(-?)(?:"([^"]+)"|(\S+))')
_WORD_RE = _re.compile(r"\w+", _re.UNICODE)


def parse_query(query: str):
    """Split a search string into ``(include, exclude)`` lists of ``(scope, text)``.
    Tokens are whitespace separated; ``"quoted phrases"`` stay together; a leading ``-``
    excludes; ``#tag`` / ``tag:x`` restrict to tags, ``name:x`` to names, ``note:x`` to content."""
    inc, exc = [], []
    for neg, phrase, word in _QUERY_RE.findall(query or ""):
        text = (phrase or word).strip()
        scope = "any"
        if not phrase:
            if text.startswith("#") and len(text) > 1:
                scope, text = "tags", text[1:]
            else:
                for prefix, sc in (("tag:", "tags"), ("name:", "name"), ("note:", "content")):
                    if text.lower().startswith(prefix) and len(text) > len(prefix):
                        scope, text = sc, text[len(prefix):]
                        break
        if text:
            (exc if neg else inc).append((scope, text.lower()))
    return inc, exc


def _token_score(token: str, scope: str, fields, allow_subsequence=True) -> float:
    """Best score of ``token`` over ``fields`` = ``[(kind, weight, text)]``:
    whole word 3.0, word prefix 2.4, substring 1.5, abbreviation inside one word of a
    name/tag 0.8 (all x field weight). Content fields never match by subsequence."""
    best = 0.0
    wanted = {"tags": "tags", "name": "name", "content": "content"}.get(scope)
    for kind, weight, text in fields:
        if wanted is not None and kind != wanted:
            continue
        low = (text or "").lower()
        if not low:
            continue
        words = _WORD_RE.findall(low)
        if token in words:
            s = 3.0
        elif any(w.startswith(token) for w in words):
            s = 2.4
        elif token in low:
            s = 1.5
        elif (allow_subsequence and kind in ("name", "tags") and len(token) >= 3
              and any(w[0] == token[0] and fuzzy_match(token, w) for w in words)):
            s = 0.8
        else:
            continue
        best = max(best, s * weight)
    return best


def smart_score(query: str, fields) -> float:
    """Specific, ranked matching for the search boxes. ``fields`` is a list of
    ``(kind, weight, text)`` with kind ``"name"`` / ``"tags"`` / ``"content"``. Every token
    of the query must match (AND); excluded tokens (``-word``) must not. Returns 0.0 for
    no match, 1.0 for an empty query, else a score (higher = better)."""
    inc, exc = parse_query(query)
    if not inc and not exc:
        return 1.0
    total = 0.0
    for scope, tok in inc:
        s = _token_score(tok, scope, fields)
        if s <= 0:
            return 0.0
        total += s
    for scope, tok in exc:
        if _token_score(tok, scope, fields, allow_subsequence=False) > 0:
            return 0.0
    return total or 1.0
