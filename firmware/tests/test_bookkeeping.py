import pytest
from firmware.core.bookkeeping import (
    _merge,
    is_replay,
    read_watermark,
    advance_watermark,
    read_used_ranges,
    append_used_range,
)

_CID = "a1b2c3d4"  # dummy contact_id used throughout


class _FakeHal:
    """Minimal in-memory HAL stub for bookkeeping tests."""

    def __init__(self):
        self._store = {}

    def read_secret(self, path):
        if path not in self._store:
            raise OSError(f"not found: {path}")
        return self._store[path]

    def write_secret(self, path, data):
        self._store[path] = data


# ── _merge ────────────────────────────────────────────────────────────────────

def test_merge_empty():
    assert _merge([]) == []


def test_merge_single():
    assert _merge([[5, 10]]) == [[5, 10]]


def test_merge_non_overlapping():
    result = _merge([[0, 5], [10, 15]])
    assert result == [[0, 5], [10, 15]]


def test_merge_adjacent():
    assert _merge([[0, 5], [5, 10]]) == [[0, 10]]


def test_merge_overlapping():
    assert _merge([[0, 10], [5, 15]]) == [[0, 15]]


def test_merge_one_inside_other():
    assert _merge([[0, 20], [5, 10]]) == [[0, 20]]


def test_merge_unsorted_input():
    result = _merge([[10, 20], [0, 5], [3, 12]])
    assert result == [[0, 5], [3, 12], [10, 20]] or result == [[0, 20]]
    # After sort+merge the two overlapping groups collapse correctly
    assert result == [[0, 20]]


def test_merge_multiple_groups():
    result = _merge([[0, 5], [3, 8], [20, 30], [25, 35]])
    assert result == [[0, 8], [20, 35]]


# ── is_replay ─────────────────────────────────────────────────────────────────

def test_is_replay_empty_ranges():
    assert not is_replay([], 0, 100)


def test_is_replay_no_overlap():
    assert not is_replay([[0, 50], [100, 150]], 60, 90)


def test_is_replay_exact_overlap():
    assert is_replay([[100, 200]], 100, 200)


def test_is_replay_partial_overlap_left():
    assert is_replay([[50, 150]], 0, 100)


def test_is_replay_partial_overlap_right():
    assert is_replay([[50, 150]], 100, 200)


def test_is_replay_contained_within():
    assert is_replay([[0, 500]], 100, 200)


def test_is_replay_adjacent_no_overlap():
    # [0, 50) and [50, 100) are adjacent but not overlapping
    assert not is_replay([[0, 50]], 50, 100)


# ── watermark read/advance ────────────────────────────────────────────────────

def test_read_watermark_missing_returns_zero():
    hal = _FakeHal()
    assert read_watermark(hal, _CID) == 0


def test_advance_watermark_from_zero():
    hal = _FakeHal()
    result = advance_watermark(hal, _CID, 508)
    assert result == 508
    assert read_watermark(hal, _CID) == 508


def test_advance_watermark_accumulates():
    hal = _FakeHal()
    advance_watermark(hal, _CID, 100)
    advance_watermark(hal, _CID, 200)
    assert read_watermark(hal, _CID) == 300


def test_watermark_isolated_per_contact():
    hal = _FakeHal()
    advance_watermark(hal, "aaaaaaaa", 100)
    advance_watermark(hal, "bbbbbbbb", 999)
    assert read_watermark(hal, "aaaaaaaa") == 100
    assert read_watermark(hal, "bbbbbbbb") == 999


# ── used-ranges read/append ───────────────────────────────────────────────────

def test_read_used_ranges_missing_returns_empty():
    hal = _FakeHal()
    assert read_used_ranges(hal, _CID) == []


def test_append_used_range_first_entry():
    hal = _FakeHal()
    result = append_used_range(hal, _CID, 0, 508)
    assert result == [[0, 508]]


def test_append_used_range_merges_adjacent():
    hal = _FakeHal()
    append_used_range(hal, _CID, 0, 100)
    result = append_used_range(hal, _CID, 100, 200)
    assert result == [[0, 200]]


def test_append_used_range_keeps_gaps():
    hal = _FakeHal()
    append_used_range(hal, _CID, 0, 100)
    result = append_used_range(hal, _CID, 200, 300)
    assert result == [[0, 100], [200, 300]]


def test_ranges_isolated_per_contact():
    hal = _FakeHal()
    append_used_range(hal, "aaaaaaaa", 0, 100)
    append_used_range(hal, "bbbbbbbb", 500, 600)
    assert read_used_ranges(hal, "aaaaaaaa") == [[0, 100]]
    assert read_used_ranges(hal, "bbbbbbbb") == [[500, 600]]
