from __future__ import annotations

import shutil

import pytest
from PIL import Image, ImageDraw, ImageFont

from rag_platform.adapters.outbound.ocr import TesseractOcrAdapter


def test_real_tesseract_reports_language_and_word_geometry() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("Tesseract is a deployment dependency and is not installed on this host")
    image = Image.new("RGB", (900, 180), "white")
    font = ImageFont.truetype("DejaVuSans.ttf", 64)
    ImageDraw.Draw(image).text(
        (30, 45), "ALARM RESET INSPECTION", font=font, fill="black", stroke_width=1
    )
    adapter = TesseractOcrAdapter()
    assert "eng" in adapter.available_languages()
    result = adapter.recognize(image, language="eng", timeout_seconds=10)
    assert result.engine_name == "tesseract"
    assert result.words
    assert all(word.bounding_box.x1 > word.bounding_box.x0 for word in result.words)
