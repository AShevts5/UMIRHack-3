"""Не вверх ли ногами: выбираем ориентацию страницы по уверенности OCR.

``enhance.py`` кладёт строки горизонтально, но по одним строкам не отличить ровный
лист от перевёрнутого (и, на редкой странице без явных строк, лежащий боком). Зато
это сразу видно по OCR: перевёрнутый русский текст Tesseract читает как мусор с
уверенностью около 50%, а ровный — с уверенностью 85–95%.

Поэтому сначала распознаём как есть. Если результат хороший — ничего не делаем, и
нормальные страницы не платят ни миллисекунды. Если плохой — пробуем 180°, а если и
это не помогло, то 90° в обе стороны, и берём вариант, где уверенно прочитанных букв
заметно больше.
"""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from . import ocr as ocr_service
from .loader import DocumentImage

#: Ниже этой средней уверенности или этого числа уверенных букв результат считаем
#: подозрительным. На открытом наборе у ровных страниц 86–95% и 250–500 букв, у
#: перевёрнутых — 51–60% и до 55 букв; у ровных фото с телефона — от 74%.
SUSPICIOUS_CONF = 70.0
SUSPICIOUS_CHARS = 150
#: Боком (±90°) пробуем, только если и после разворота на 180° почти ничего не
#: прочитано: направление строк ``enhance.py`` и так находит, это страховка.
HOPELESS_CHARS = 60
#: Другая ориентация побеждает, только если уверенных букв в ней заметно больше.
WIN_RATIO = 1.5
WIN_MARGIN = 30


def confident_chars(result: ocr_service.OcrResult) -> int:
    """Сколько букв в словах, которые OCR прочитал уверенно (от 70%, от трёх букв)."""
    return sum(
        len(word.text)
        for word in result.words
        if word.conf >= 70 and sum(ch.isalpha() for ch in word.text) >= 3
    )


def looks_wrong(result: ocr_service.OcrResult) -> bool:
    return result.mean_conf < SUSPICIOUS_CONF or confident_chars(result) < SUSPICIOUS_CHARS


def turn_image(image: DocumentImage, quarter_turns: int) -> DocumentImage:
    """Поворот на k×90° против часовой стрелки, без потерь (просто перестановка пикселей)."""
    rgb = np.ascontiguousarray(np.rot90(image.rgb, quarter_turns))
    return replace(image, rgb=rgb, grey=cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))


def ocr_upright(image: DocumentImage) -> tuple[DocumentImage, ocr_service.OcrResult, int]:
    """OCR страницы в правильной ориентации.

    Возвращает (страница, результат OCR, на сколько градусов против часовой мы её
    докрутили: 0, 90, 180 или 270).
    """
    best_image, best = image, ocr_service.run_ocr(image)
    if not looks_wrong(best):
        return best_image, best, 0
    best_chars, best_turn = confident_chars(best), 0
    for turns in ((2,), (1, 3)):
        if turns != (2,) and best_chars >= HOPELESS_CHARS:
            break
        for k in turns:
            candidate = turn_image(image, k)
            result = ocr_service.run_ocr(candidate)
            chars = confident_chars(result)
            if chars > WIN_RATIO * best_chars + WIN_MARGIN:
                best_image, best, best_chars, best_turn = candidate, result, chars, k * 90
        if not looks_wrong(best):
            break
    return best_image, best, best_turn
