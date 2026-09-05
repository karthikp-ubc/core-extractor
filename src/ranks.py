#!/usr/bin/env python3
"""Canonical CORE rank ordering — SPEC.md §9: A* > A > B > Australasian B > C.

Small enough to not warrant its own "stage", but shared by stage4_join.py's
coverage summary and (eventually) app.py's plot ordering, so it lives here
rather than being duplicated.
"""

RANK_ORDER = ["A*", "A", "B", "Australasian B", "C"]


def rank_key(rank) -> int:
    """Sort key so higher-ranked venues sort first. Anything not in
    RANK_ORDER (e.g. "Unranked", "unranked: merged", "National: USA",
    None) sorts after all real ranks, in the order RANK_ORDER.index would
    otherwise raise on."""
    if rank in RANK_ORDER:
        return RANK_ORDER.index(rank)
    return len(RANK_ORDER)


def sort_by_rank(items, key=lambda x: x):
    return sorted(items, key=lambda x: rank_key(key(x)))
