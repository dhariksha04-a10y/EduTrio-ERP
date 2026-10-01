from __future__ import annotations
import io
import logging
import shutil
from dataclasses import dataclass
import cv2
import numpy as np
from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)


@dataclass
class OCRResult:
    text: str
    confidence: float


def preprocess(data: bytes) -> np.ndarray:
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("File is not a readable image") from exc
    arr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    h, w = arr.shape[:2]
    if min(h, w) < 400:
        scale = min(3.0, 400 / min(h, w))
        arr = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 11)


def read_text(data: bytes) -> OCRResult:
    image = preprocess(data)
    try:
        import easyocr
        if not hasattr(read_text, "_reader"):
            read_text._reader = easyocr.Reader(["en"], gpu=False)
        lines = read_text._reader.readtext(image, detail=1, paragraph=False)
        text = "\n".join(str(x[1]) for x in lines)
        scores = [float(x[2]) for x in lines if len(x) > 2]
        confidence = float(np.mean(scores)) if scores else 0.0
        if text.strip() and confidence >= 0.30:
            return OCRResult(text, confidence)
    except Exception as exc:
        log.info("EasyOCR unavailable or failed; trying Tesseract (%s)", type(exc).__name__)
    try:
        import pytesseract
        if shutil.which("tesseract") is None:
            raise RuntimeError("Tesseract executable is not installed")
        out = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        words, scores = [], []
        for word, conf in zip(out["text"], out["conf"]):
            if word.strip():
                words.append(word)
                try:
                    score = float(conf)
                    if score >= 0:
                        scores.append(score / 100)
                except (TypeError, ValueError):
                    pass
        return OCRResult(" ".join(words), float(np.mean(scores)) if scores else 0.0)
    except Exception as exc:
        raise RuntimeError("OCR failed: install EasyOCR model dependencies or Tesseract") from exc
