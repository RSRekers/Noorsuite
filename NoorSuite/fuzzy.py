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
