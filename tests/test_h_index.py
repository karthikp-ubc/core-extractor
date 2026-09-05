import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stage3_verify import h_index  # noqa: E402


def test_classic_example():
    # 4 papers each with >=4 citations, 5th paper falls short -> h=4
    assert h_index([10, 8, 5, 4, 3]) == 4
    # only 3 papers reach >=3 citations
    assert h_index([25, 8, 5, 3, 3]) == 3


def test_all_zero():
    assert h_index([0, 0, 0]) == 0


def test_empty():
    assert h_index([]) == 0


def test_single_paper():
    assert h_index([100]) == 1
    assert h_index([0]) == 0


def test_h_index_five():
    assert h_index([9, 7, 6, 5, 5, 3, 1]) == 5


def test_order_independent():
    counts = [3, 30, 1, 8, 5]
    assert h_index(counts) == h_index(sorted(counts))
