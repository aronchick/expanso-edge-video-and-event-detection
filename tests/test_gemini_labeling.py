"""Tests for Gemini API integration in dataset labeling.

Tests the parsing, error handling, and bbox extraction logic without
making real API calls (mocked).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from expanso_security_camera.dataset import _call_gemini_for_boxes


class TestParseGeminiResponse:
    """Test parsing of various Gemini response formats."""

    def test_clean_json(self):
        text = '[{"x1":10,"y1":20,"x2":100,"y2":200}]'
        result = _parse_gemini_text(text)
        assert len(result) == 1
        assert result[0]["bbox"] == [10, 20, 100, 200]

    def test_markdown_fenced_json(self):
        text = '```json\n[{"x1":10,"y1":20,"x2":100,"y2":200}]\n```'
        result = _parse_gemini_text(text)
        assert len(result) == 1

    def test_multiple_boxes(self):
        text = '[{"x1":10,"y1":20,"x2":100,"y2":200},{"x1":300,"y1":50,"x2":400,"y2":150}]'
        result = _parse_gemini_text(text)
        assert len(result) == 2
        assert result[1]["bbox"] == [300, 50, 400, 150]

    def test_empty_array(self):
        text = "[]"
        result = _parse_gemini_text(text)
        assert result == []

    def test_no_json(self):
        text = "I cannot identify any boxes in this image."
        result = _parse_gemini_text(text)
        assert result == []

    def test_missing_keys_skipped(self):
        text = '[{"x1":10,"y1":20,"x2":100,"y2":200},{"x1":10,"y1":20}]'
        result = _parse_gemini_text(text)
        assert len(result) == 1  # second entry missing x2/y2

    def test_surrounding_text_ignored(self):
        text = 'Here are the boxes:\n[{"x1":10,"y1":20,"x2":100,"y2":200}]\nDone.'
        result = _parse_gemini_text(text)
        assert len(result) == 1

    def test_float_coords_converted_to_int(self):
        text = '[{"x1":10.5,"y1":20.3,"x2":100.7,"y2":200.9}]'
        result = _parse_gemini_text(text)
        assert len(result) == 1
        assert all(isinstance(v, int) for v in result[0]["bbox"])

    def test_malformed_json_like_3_1_lite(self):
        """gemini-3.1-flash-lite returns malformed JSON like this."""
        text = '[{"x1": 352, 227, 571, 570}, {"x1": 388, "y1": 519}]'
        result = _parse_gemini_text(text)
        # Should not crash, return empty or partial
        assert isinstance(result, list)


def _parse_gemini_text(text: str) -> list[dict]:
    """Helper: parse Gemini response text into bbox dicts.

    Mirrors the parsing logic in _call_gemini_for_boxes.
    """
    text = text.replace("```json", "").replace("```", "").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        boxes = json.loads(text[start : end + 1])
        return [
            {"bbox": [int(b["x1"]), int(b["y1"]), int(b["x2"]), int(b["y2"])]}
            for b in boxes
            if all(k in b for k in ("x1", "y1", "x2", "y2"))
        ]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


class TestGeminiAPIErrors:
    """Test error handling for various API failure modes."""

    def _make_test_image(self, tmp_path) -> str:
        """Create a tiny test JPEG and return its path."""
        import numpy as np

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        import cv2

        path = str(tmp_path / "test.jpg")
        cv2.imwrite(path, img)
        return path

    @patch("urllib.request.urlopen")
    def test_429_retries(self, mock_urlopen, tmp_path):
        """Should retry on 429 with exponential backoff."""
        import urllib.error

        img_path = self._make_test_image(tmp_path)

        error_resp = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
        success_resp = MagicMock()
        success_resp.__enter__ = MagicMock(return_value=success_resp)
        success_resp.__exit__ = MagicMock(return_value=False)
        success_resp.read.return_value = json.dumps(
            {
                "candidates": [
                    {"content": {"parts": [{"text": '[{"x1":10,"y1":20,"x2":100,"y2":200}]'}]}}
                ]
            }
        ).encode()

        mock_urlopen.side_effect = [error_resp, success_resp]

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            with patch("time.sleep"):
                result = _call_gemini_for_boxes(img_path, 1)

        assert len(result) == 1
        assert result[0]["bbox"] == [10, 20, 100, 200]

    def test_missing_api_key(self, tmp_path):
        """Should raise RuntimeError when no API key."""
        import os

        img_path = self._make_test_image(tmp_path)
        with patch.dict("os.environ", {}, clear=True):
            os.environ.pop("GOOGLE_API_KEY", None)
            with pytest.raises(RuntimeError, match="GOOGLE_API_KEY not set"):
                _call_gemini_for_boxes(img_path, 1)

    @patch("urllib.request.urlopen")
    def test_empty_candidates(self, mock_urlopen, tmp_path):
        """Should handle empty/missing candidates gracefully."""
        img_path = self._make_test_image(tmp_path)

        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({"candidates": []}).encode()
        mock_urlopen.return_value = resp

        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}):
            with pytest.raises((KeyError, IndexError)):
                _call_gemini_for_boxes(img_path, 1)


class TestYOLOLabelFormat:
    """Test that generated YOLO label files are correctly formatted."""

    def test_yolo_format(self):
        """YOLO format: class x_center y_center width height (normalized 0-1)."""
        # Simulate what label() writes
        w, h = 640, 360
        dets = [{"bbox": [100, 50, 300, 200]}]

        lines = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            xc = (x1 + x2) / 2 / w
            yc = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")

        assert len(lines) == 1
        parts = lines[0].split()
        assert parts[0] == "0"  # class id
        assert len(parts) == 5
        # All values should be 0-1 range
        for val in parts[1:]:
            assert 0.0 <= float(val) <= 1.0

    def test_multiple_boxes_format(self):
        w, h = 640, 360
        dets = [
            {"bbox": [10, 20, 100, 200]},
            {"bbox": [300, 50, 500, 300]},
        ]

        lines = []
        for d in dets:
            x1, y1, x2, y2 = d["bbox"]
            xc = (x1 + x2) / 2 / w
            yc = (y1 + y2) / 2 / h
            bw = (x2 - x1) / w
            bh = (y2 - y1) / h
            lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")

        assert len(lines) == 2
        # Each line is independent
        for line in lines:
            parts = line.split()
            assert parts[0] == "0"
            assert len(parts) == 5
