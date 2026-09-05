import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ranks import rank_key, sort_by_rank  # noqa: E402


def test_canonical_order():
    ranks = ["C", "A*", "Australasian B", "A", "B"]
    assert sort_by_rank(ranks) == ["A*", "A", "B", "Australasian B", "C"]


def test_unknown_ranks_sort_last_and_stable():
    ranks = ["B", "Unranked", "A*", "unranked: merged", "National: USA"]
    ordered = sort_by_rank(ranks)
    assert ordered[:2] == ["A*", "B"]
    assert set(ordered[2:]) == {"Unranked", "unranked: merged", "National: USA"}


def test_none_sorts_last():
    assert rank_key(None) == len(["A*", "A", "B", "Australasian B", "C"])
