"""Tests for dataset.py — label parsing, CLI args, validation helpers."""

from __future__ import annotations

from expanso_security_camera.dataset import _parse_arg, _parse_indices


class TestParseIndices:
    def test_simple_array(self):
        assert _parse_indices("[1, 3, 5]") == [0, 2, 4]  # 1-based → 0-based

    def test_with_surrounding_text(self):
        assert _parse_indices("Here are the boxes: [2, 4, 6]") == [1, 3, 5]

    def test_empty_array(self):
        assert _parse_indices("[]") == []

    def test_no_array(self):
        assert _parse_indices("no boxes found") == []

    def test_non_integer_ignored(self):
        assert _parse_indices('[1, "two", 3]') == [0, 2]

    def test_zero_and_negative_ignored(self):
        assert _parse_indices("[0, -1, 1, 2]") == [0, 1]

    def test_single_element(self):
        assert _parse_indices("[5]") == [4]

    def test_whitespace(self):
        assert _parse_indices("  [ 1 , 2 , 3 ]  ") == [0, 1, 2]


class TestParseArg:
    def test_found(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cmd", "sub", "--boxes", "8"])
        assert _parse_arg("--boxes", 4, int) == 8

    def test_not_found_uses_default(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cmd", "sub"])
        assert _parse_arg("--boxes", 4, int) == 4

    def test_float_cast(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cmd", "--fps", "2.5"])
        assert _parse_arg("--fps", 5.0, float) == 2.5

    def test_string_cast(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cmd", "--camera", "cam-inside"])
        assert _parse_arg("--camera", "cam-outside", str) == "cam-inside"

    def test_flag_at_end_without_value(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["cmd", "--boxes"])
        assert _parse_arg("--boxes", 4, int) == 4
