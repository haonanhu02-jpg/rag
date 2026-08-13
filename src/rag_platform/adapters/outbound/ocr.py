"""Tesseract OCR adapter behind the parser-neutral OCR port."""

from __future__ import annotations

from collections import defaultdict

import pytesseract
from PIL import Image
from pytesseract import Output

from rag_platform.modules.knowledge.contracts import (
    BoundingBox,
    CoordinateSpace,
    DocumentParseError,
    OcrResult,
    OcrWord,
)


class TesseractOcrAdapter:
    def available_languages(self) -> frozenset[str]:
        try:
            return frozenset(pytesseract.get_languages(config=""))
        except pytesseract.TesseractNotFoundError as exc:
            raise DocumentParseError(
                "ocr_unavailable", "Tesseract executable is unavailable"
            ) from exc

    def recognize(
        self, image: object, *, language: str, timeout_seconds: float
    ) -> OcrResult:
        if not isinstance(image, Image.Image):
            raise TypeError("OCR expects a Pillow image")
        if language not in self.available_languages():
            raise DocumentParseError(
                "ocr_language_unavailable", f"OCR language is unavailable: {language}"
            )
        try:
            data = pytesseract.image_to_data(
                image,
                lang=language,
                output_type=Output.DICT,
                timeout=timeout_seconds,
            )
        except RuntimeError as exc:
            raise DocumentParseError("ocr_timeout", "OCR exceeded its time limit") from exc
        except pytesseract.TesseractError as exc:
            raise DocumentParseError(
                "ocr_failed", "Tesseract could not recognize the image"
            ) from exc
        words: list[OcrWord] = []
        for index, raw in enumerate(data["text"]):
            value = str(raw).strip()
            confidence = float(data["conf"][index])
            if not value or confidence < 0:
                continue
            left, top = int(data["left"][index]), int(data["top"][index])
            width, height = int(data["width"][index]), int(data["height"][index])
            if width < 1 or height < 1:
                continue
            words.append(
                OcrWord(
                    value,
                    min(1.0, confidence / 100.0),
                    len(words),
                    BoundingBox(
                        float(left),
                        float(top),
                        float(left + width),
                        float(top + height),
                        CoordinateSpace.PIXELS,
                    ),
                )
            )
        try:
            version = str(pytesseract.get_tesseract_version())
        except pytesseract.TesseractNotFoundError:
            version = "unknown"
        return OcrResult("tesseract", version, language, tuple(words))


def ocr_lines(result: OcrResult) -> tuple[tuple[str, BoundingBox, float], ...]:
    """Create stable line-like blocks while retaining word-level union geometry."""
    if not result.words:
        return ()
    grouped: defaultdict[int, list[OcrWord]] = defaultdict(list)
    for word in result.words:
        grouped[int(word.bounding_box.y0 // 16)].append(word)
    values: list[tuple[str, BoundingBox, float]] = []
    for words in grouped.values():
        ordered = sorted(words, key=lambda item: (item.bounding_box.x0, item.order))
        values.append(
            (
                " ".join(word.text for word in ordered),
                BoundingBox(
                    min(word.bounding_box.x0 for word in words),
                    min(word.bounding_box.y0 for word in words),
                    max(word.bounding_box.x1 for word in words),
                    max(word.bounding_box.y1 for word in words),
                    words[0].bounding_box.coordinate_space,
                ),
                sum(word.confidence for word in words) / len(words),
            )
        )
    return tuple(values)
