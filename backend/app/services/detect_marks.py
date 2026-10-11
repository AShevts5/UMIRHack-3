"""Поиск графических подписи и печати.

Главное правило из rules.md 2.3: напечатанное слово «Подпись» или «М.П.» — это ещё
не подпись и не печать. Поэтому текст тут используется только чтобы найти место
(зону), а решение принимается по пикселям этой зоны.

Точка входа: ``detect_marks(image, ocr_boxes, document_type)``.

Сигналов два, чтобы результат не висел на одном признаке:

``colour``
    Подпись и макет печати нарисованы синим, а печатный текст чёрный. Маска по
    синему каналу (синий заметно больше красного и зелёного) вытаскивает именно
    чернила. Это анализ картинки, а не поиск слова «Подпись».
``geometry``
    Для чёрно-белых сканов те же зоны смотрим в сером: у рукописного штриха
    отношение периметра к площади («лохматость») большое, а у печатных букв,
    линии для подписи и линеек заполнения — маленькое. Печать ищем как крупную
    кольцевую компоненту, при возможности подтверждаем кругами Хафа.

Положительные и отрицательные примеры для всех трёх типов лежат в
``tests/test_signatures.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..config import settings
from ..utils.textnorm import norm_text
from .document_config import DocumentTypeSpec, MarkSpec
from .loader import DocumentImage
from .ocr import OcrResult, TextLine, Word

# ---------------------------------------------------------------------------
# Пороги подбирали на открытом наборе v1.0 (см. README «Проверка качества»)
# ---------------------------------------------------------------------------
BLUE_CHROMA_DIFF = 28  # насколько синий должен быть ярче красного, чтобы счесть пиксель синими чернилами
BLUE_GREEN_DIFF = 8  # то же самое для зелёного канала
BLUE_MIN_VALUE = 60
MIN_MARK_PIXELS = 90  # меньше — это уже мусор от OCR, а не штрих
MIN_MARK_WIDTH = 45
MIN_MARK_HEIGHT = 5
MAX_MARK_HEIGHT_RATIO = 0.22  # «подпись» выше этой доли страницы — уже картинка
MAX_MARK_WIDTH_RATIO = 0.45
MIN_SEAL_DIAMETER_RATIO = 0.045
MAX_SEAL_DIAMETER_RATIO = 0.30
SEAL_MIN_FILL = 0.012  # доля закрашенных пикселей в рамке: у кольца печати она маленькая

_HANDWRITING_HINTS = ("подпис", "одпись", "подпи", "подгис", "signature")
_SEAL_HINTS = ("печат", "м.п", "м п", "штамп", "seal")


@dataclass
class Zone:
    """Прямоугольник в пикселях (x, y, w, h) вместе со словами, по которым его нашли."""

    x: int
    y: int
    w: int
    h: int
    anchors: list[str] = field(default_factory=list)

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(self.w, 0) * max(self.h, 0)

    def as_box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def intersects(self, other: "Zone", pad: int = 0) -> bool:
        return not (
            self.x2 + pad <= other.x
            or other.x2 + pad <= self.x
            or self.y2 + pad <= other.y
            or other.y2 + pad <= self.y
        )

    def expanded(self, pad_x: int, pad_y: int, bounds: tuple[int, int]) -> "Zone":
        width, height = bounds
        x = max(0, self.x - pad_x)
        y = max(0, self.y - pad_y)
        x2 = min(width, self.x2 + pad_x)
        y2 = min(height, self.y2 + pad_y)
        return Zone(x=x, y=y, w=max(0, x2 - x), h=max(0, y2 - y), anchors=list(self.anchors))


# ---------------------------------------------------------------------------
# Маски по цвету
# ---------------------------------------------------------------------------


def blue_ink_mask(rgb: np.ndarray) -> np.ndarray:
    """Маска синих пикселей: так вытаскиваем чернила подписи и макета печати."""
    data = rgb.astype(np.int16)
    red, green, blue = data[..., 0], data[..., 1], data[..., 2]
    mask = (
        ((blue - red) > BLUE_CHROMA_DIFF)
        & ((blue - green) > BLUE_GREEN_DIFF)
        & (blue > BLUE_MIN_VALUE)
    )
    return mask


def dark_ink_mask(grey: np.ndarray, threshold: int = 150) -> np.ndarray:
    """Маска тёмных пикселей — запасной вариант для чёрно-белых сканов."""
    return grey < threshold


# ---------------------------------------------------------------------------
# Якоря: слова, по которым находим место
# ---------------------------------------------------------------------------


def _word_text_pairs(ocr: OcrResult) -> list[Word]:
    return [word for word in ocr.words if word.text.strip()]


def find_signature_words(ocr: OcrResult) -> list[Word]:
    """Слова с напечатанным «Подпись» — только чтобы найти место, а не как доказательство."""
    hits: list[Word] = []
    for word in _word_text_pairs(ocr):
        low = norm_text(word.text)
        if any(hint in low for hint in _HANDWRITING_HINTS) and len(low) >= 5:
            hits.append(word)
    return hits


def find_seal_words(ocr: OcrResult) -> list[Word]:
    hits: list[Word] = []
    for word in _word_text_pairs(ocr):
        low = norm_text(word.text).strip(" .:;,")
        if low in {"м.п", "мп", "м п", "место печати", "печать"}:
            hits.append(word)
            continue
        if any(hint in low for hint in _SEAL_HINTS) and len(low) >= 4:
            hits.append(word)
    return hits


def _lines_with(ocr: OcrResult, hints: tuple[str, ...]) -> list[TextLine]:
    """Строки, где встречается любой из ``hints`` (нужно для диагностики и тестов)."""
    found = []
    for line in ocr.lines:
        low = norm_text(line.text)
        if any(hint in low for hint in hints):
            found.append(line)
    return found


#: Печатные подписи печати, которые OCR иногда приклеивает к строке с «Подпись».
_SEAL_PRINT_RE = re.compile(r"^(м\.?\s?п\.?|печать|печ|макет|штамп|ученая|учебная)$")


def _is_seal_print(word: Word) -> bool:
    return bool(_SEAL_PRINT_RE.match(norm_text(word.text).strip(". :;,")))


# ---------------------------------------------------------------------------
# Геометрия: линии и компоненты
# ---------------------------------------------------------------------------


def find_horizontal_rules(
    grey: np.ndarray,
    min_length_ratio: float = 0.06,
    max_thickness: int = 6,
) -> list[Zone]:
    """Ищем длинные тонкие горизонтальные отрезки: линию для подписи и линейки заполнения."""
    height, width = grey.shape
    min_length = max(40, int(width * min_length_ratio))
    dark = (grey < 190).astype(np.uint8)
    opened = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (min_length, 1))
    )
    count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    rules: list[Zone] = []
    for index in range(1, count):
        x, y, w, h, _ = stats[index]
        if w >= min_length and h <= max_thickness:
            rules.append(Zone(x=int(x), y=int(y), w=int(w), h=int(h), anchors=["rule"]))
    return rules


def _roughness(mask: np.ndarray) -> float:
    """Периметр² / площадь самой крупной компоненты: у рукописного штриха это число большое."""
    if mask.sum() == 0:
        return 0.0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return 0.0
    best = 0.0
    for index in range(1, count):
        area = float(stats[index, cv2.CC_STAT_AREA])
        if area < 40:
            continue
        component = (mask & 0).astype(np.uint8)
        component = np.zeros_like(mask, dtype=np.uint8)
        component[stats[index, cv2.CC_STAT_TOP] : stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT],
                  stats[index, cv2.CC_STAT_LEFT] : stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH]] = (
            mask[
                stats[index, cv2.CC_STAT_TOP] : stats[index, cv2.CC_STAT_TOP] + stats[index, cv2.CC_STAT_HEIGHT],
                stats[index, cv2.CC_STAT_LEFT] : stats[index, cv2.CC_STAT_LEFT] + stats[index, cv2.CC_STAT_WIDTH],
            ]
        ).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        perimeter = float(sum(cv2.arcLength(contour, True) for contour in contours))
        best = max(best, (perimeter * perimeter) / max(area, 1.0))
    return best


def _component_stats(mask: np.ndarray, min_area: int = 12) -> list[dict]:
    if mask.sum() == 0:
        return []
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    items = []
    for index in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[index])
        if area < min_area:
            continue
        items.append({"x": x, "y": y, "w": w, "h": h, "area": area})
    return items


def _merge_boxes(boxes: list[dict], gap_x: int, gap_y: int) -> dict | None:
    """Склеиваем близкие компоненты: подпись состоит из отдельных штрихов."""
    if not boxes:
        return None
    boxes = sorted(boxes, key=lambda item: (item["y"], item["x"]))
    group = dict(boxes[0])
    changed = True
    while changed:
        changed = False
        for item in boxes:
            if item is group:
                continue
            if (
                item["x"] <= group["x"] + group["w"] + gap_x
                and group["x"] <= item["x"] + item["w"] + gap_x
                and item["y"] <= group["y"] + group["h"] + gap_y
                and group["y"] <= item["y"] + item["h"] + gap_y
            ):
                x1 = min(group["x"], item["x"])
                y1 = min(group["y"], item["y"])
                x2 = max(group["x"] + group["w"], item["x"] + item["w"])
                y2 = max(group["y"] + group["h"], item["y"] + item["h"])
                if (x1, y1, x2, y2) != (
                    group["x"],
                    group["y"],
                    group["x"] + group["w"],
                    group["y"] + group["h"],
                ):
                    group.update({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
                    group["area"] = group.get("area", 0) + item.get("area", 0)
                    changed = True
    return group


# ---------------------------------------------------------------------------
# Строим зоны
# ---------------------------------------------------------------------------


def signature_zone(
    image: DocumentImage,
    ocr: OcrResult,
    spec: DocumentTypeSpec,
    seal_zone: Zone | None = None,
) -> Zone | None:
    """Зона, где ждём подпись.

    Абсолютные координаты страницы не используем, привязываемся к напечатанному:

    * начинаем **после последнего слова подписи**, чтобы буквы «Подпись» не попали
      в зону и не сошли за рукопись;
    * если рядом напечатана линия для подписи, берём её границы — для места подписи
      это самый надёжный ориентир;
    * в рамку печати зона никогда не заходит.
    """
    height, width = image.height, image.width
    words = [word for word in find_signature_words(ocr) if word.cy >= height * 0.45]
    if not words:
        return _signature_zone_from_rule(image, seal_zone)
    label = words[0]
    line = ocr.line_at(label.cy)
    raw_words = line.words if line is not None else [label]
    # OCR часто приклеивает к строке подписи печатные «ПЕЧАТЬ» и «МАКЕТ»: это не
    # часть подписи, и сдвигать зону они не должны.
    caption_words = [word for word in raw_words if not _is_seal_print(word)]
    if not caption_words:
        caption_words = [label]
    # Берём только слова рядом с «Подписью», иначе далёкий текстовый блок в той же
    # OCR-строке растянет подпись.
    limit_x = label.left + max(label.width, 40) * 3.2
    caption_words = [word for word in caption_words if word.left <= limit_x] or [label]
    caption_bottom = max(word.bottom for word in caption_words)
    caption_height = max(
        max(word.height for word in caption_words),
        line.height if line is not None else label.height,
        12,
    )
    caption_right = min(max(word.right for word in caption_words), width)

    top = max(0, int(caption_bottom - caption_height * 2.0))
    bottom = min(height, int(caption_bottom + caption_height * 1.1))

    left = max(0, caption_right + 6)
    right = width
    if seal_zone is not None:
        # Макет печати не должен попасть в подпись.
        right = max(left + 40, min(right, seal_zone.x - 6))
    zone = Zone(x=left, y=top, w=max(0, right - left), h=max(0, bottom - top), anchors=[label.text])

    # Границы зоны лучше всего задаёт напечатанная линия для подписи.
    rules = find_horizontal_rules(image.grey)
    for rule in rules:
        # Короткий обрывок — это линейка, частично закрытая самой подписью: по ней
        # зона обрежет подпись, поэтому верим только целой линии.
        if rule.w < max(80, int(width * 0.12)):
            continue
        if not (rule.y2 >= top - 6 and rule.y <= bottom + 6):
            continue
        if rule.x2 <= caption_right:
            continue
        if seal_zone is not None and rule.x >= seal_zone.x2 - 8:
            # Линейка внутри печати или правее неё — это не линия для подписи.
            continue
        zone_left = max(zone.x, rule.x - 4)
        zone_top = min(zone.y, rule.y - int(caption_height * 0.8))
        zone_right = min(right, rule.x2 + 4)
        zone_bottom = max(bottom, rule.y2 + int(caption_height * 0.5))
        zone = Zone(
            x=zone_left,
            y=zone_top,
            w=max(0, zone_right - zone_left),
            h=max(0, zone_bottom - zone_top),
            anchors=zone.anchors + ["rule"],
        )
        break
    return zone


def _signature_zone_from_rule(image: DocumentImage, seal_zone: Zone | None) -> Zone | None:
    """Запасной путь, когда OCR не увидел подпись (смазанное фото): напечатанная
    линия в нижней части страницы всё ещё показывает, где расписываются."""
    height, width = image.height, image.width
    candidates = [
        rule
        for rule in find_horizontal_rules(image.grey)
        if rule.y >= height * 0.55
        and rule.y <= height * 0.92
        and 0.10 * width <= rule.w <= 0.45 * width
        and (seal_zone is None or rule.x2 <= seal_zone.x + 8)
    ]
    if not candidates:
        # Ни подписи, ни линии: остаётся только полоса слева от печати, а детекторы
        # чернил ниже всё равно потребуют настоящий штрих.
        right = seal_zone.x - 6 if seal_zone is not None else int(width * 0.62)
        top = int(height * 0.55)
        return Zone(x=int(width * 0.03), y=top, w=max(0, right - int(width * 0.03)),
                    h=int(height * 0.37), anchors=["band"])
    rule = min(candidates, key=lambda item: item.y)
    pad_y = max(12, int(height * 0.03))
    return Zone(
        x=max(0, rule.x - 4),
        y=max(0, rule.y - pad_y),
        w=rule.w + 8,
        h=pad_y + int(height * 0.012),
        anchors=["rule"],
    )


def seal_zone(image: DocumentImage, ocr: OcrResult, spec: DocumentTypeSpec) -> Zone | None:
    """Зона макета печати: справа, вокруг самой нижней подписи печати.

    Смотрим только на нижнюю половину страницы, иначе сноска «… печать являются
    учебными макетами» утащит зону наверх.
    """
    height, width = image.height, image.width
    points = [
        word for word in find_seal_words(ocr) if word.cy >= height * 0.5
    ]
    if not points:
        return None
    # «М.П.» — специальная подпись места печати, если она есть, берём её.
    marked = [word for word in points if norm_text(word.text).strip(" .:;,") in {"м.п", "мп", "м п"}]
    if marked:
        anchor = max(marked, key=lambda word: word.cy)
        depth = int(height * 0.115)
        top = max(0, int(anchor.top - depth))
        bottom = min(height, int(anchor.bottom + height * 0.02))
        left = max(0, int(anchor.left - anchor.width * 2.2))
        if anchor.width < 3:  # pragma: no cover - OCR вернул вырожденную рамку
            left = int(width * 0.72)
        return Zone(x=left, y=top, w=max(0, width - left), h=max(0, bottom - top), anchors=[anchor.text])

    lowest = max(points, key=lambda word: word.cy)
    depth = int(height * 0.24)
    top = max(0, int(lowest.top - depth))
    bottom = min(height, int(lowest.bottom + height * 0.035))
    left = max(int(width * 0.40), int(min(word.left for word in points) - width * 0.28))
    return Zone(x=left, y=top, w=max(0, width - left), h=max(0, bottom - top), anchors=[w.text for w in points])


# ---------------------------------------------------------------------------
# Доказательства подписи
# ---------------------------------------------------------------------------


def _is_printed_glyph(item: dict, median_height: float) -> bool:
    """Проверяем, похожа ли компонента на печатную букву.

    Саму подпись отсекает геометрия зоны, но внутрь может попасть текст из другого
    варианта вёрстки, колонтитул или слова печати. Печатная буква мелкая по обеим
    сторонам, а рукописный штрих широкий (или с широкими пробелами), даже если
    короткий.
    """
    width_limit = max(MIN_MARK_WIDTH, median_height * 3.0)
    height_limit = max(24.0, median_height * 1.8)
    return item["w"] < width_limit and item["h"] < height_limit


def _signature_from_mask(
    mask: np.ndarray,
    image: DocumentImage,
    zone: Zone,
    method: str,
    min_components: int = 1,
) -> dict | None:
    """Общая проверка правдоподобности кандидата в подпись внутри ``zone``."""
    if mask.sum() == 0:
        return None
    components = _component_stats(mask, min_area=8)
    heights = [line_box["h"] for line_box in components] or [20]
    heights.sort()
    median_height = float(heights[len(heights) // 2])
    kept = [item for item in components if not _is_printed_glyph(item, median_height)]
    merged = _merge_boxes(kept, gap_x=max(14, zone.h), gap_y=max(10, zone.h // 2))
    if merged is None or len(kept) < min_components:
        return None
    pixels = int(
        mask[merged["y"] : merged["y"] + merged["h"], merged["x"] : merged["x"] + merged["w"]].sum()
    )
    width_ratio = merged["w"] / max(image.width, 1)
    height_ratio = merged["h"] / max(image.height, 1)
    plausible = (
        pixels >= MIN_MARK_PIXELS
        and merged["w"] >= MIN_MARK_WIDTH
        and merged["h"] >= MIN_MARK_HEIGHT
        and width_ratio <= MAX_MARK_WIDTH_RATIO
        and height_ratio <= MAX_MARK_HEIGHT_RATIO
    )
    return {
        "method": method,
        "pixels": pixels,
        "box": (merged["x"], merged["y"], merged["w"], merged["h"]),
        "strength": min(1.0, pixels / 600.0),
        "component_count": len(components),
        "kept_components": len(kept),
        "plausible": plausible,
    }


def _signature_from_blue(image: DocumentImage, zone: Zone, seal_zone_box: Zone | None) -> dict | None:
    blue = blue_ink_mask(image.rgb)
    mask = np.zeros_like(blue, dtype=bool)
    mask[zone.y : zone.y2, zone.x : zone.x2] = blue[zone.y : zone.y2, zone.x : zone.x2]
    if seal_zone_box is not None:
        mask[seal_zone_box.y : seal_zone_box.y2, seal_zone_box.x : seal_zone_box.x2] = False
    return _signature_from_mask(mask, image, zone, "colour")


def _signature_from_greyscale(
    image: DocumentImage,
    ocr: OcrResult,
    zone: Zone,
    seal_box: Zone | None,
    rules: list[Zone],
) -> dict | None:
    """Запасной путь для чёрно-белых сканов: ищем неровные, рукописные штрихи."""
    grey = image.grey
    zone_mask = np.zeros(grey.shape, dtype=bool)
    zone_mask[zone.y : zone.y2, zone.x : zone.x2] = True
    if seal_box is not None:
        zone_mask[seal_box.y : seal_box.y2, seal_box.x : seal_box.x2] = False
    dark = dark_ink_mask(grey) & zone_mask
    # Убираем длинные тонкие линейки: они гладкие и перекрывают чернила поверх себя.
    for rule in rules:
        if rule.intersects(zone):
            dark[rule.y - 1 : rule.y2 + 2, rule.x : rule.x2] = False
    components = _component_stats(dark, min_area=10)
    heights = sorted(item["h"] for item in components) or [20]
    median_height = float(heights[len(heights) // 2])
    kept = []
    for item in components:
        if _is_printed_glyph(item, median_height):
            continue
        patch = np.zeros_like(dark, dtype=bool)
        patch[item["y"] : item["y"] + item["h"], item["x"] : item["x"] + item["w"]] = dark[
            item["y"] : item["y"] + item["h"], item["x"] : item["x"] + item["w"]
        ]
        rough = _roughness(patch)
        fill = item["area"] / max(item["w"] * item["h"], 1)
        # Печатные слова плотные и гладкие, рукопись — разреженная и рваная.
        if fill > 0.34 and rough < 60.0:
            continue
        kept.append(item)
    if not kept:
        return None
    evidence = _signature_from_mask(dark, image, zone, "greyscale")
    if evidence is None:
        return None
    box = evidence["box"]
    patch = np.zeros_like(dark, dtype=bool)
    patch[box[1] : box[1] + box[3], box[0] : box[0] + box[2]] = dark[
        box[1] : box[1] + box[3], box[0] : box[0] + box[2]
    ]
    evidence["roughness"] = round(_roughness(patch), 2)
    return evidence


# ---------------------------------------------------------------------------
# Доказательства печати
# ---------------------------------------------------------------------------


def _circle_confirmation(mask: np.ndarray) -> int:
    if mask.sum() == 0 or min(mask.shape) < 24:
        return 0
    patch = (mask.astype(np.uint8)) * 255
    patch = cv2.morphologyEx(patch, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    circles = cv2.HoughCircles(
        patch,
        cv2.HOUGH_GRADIENT,
        dp=1.5,
        minDist=max(20, min(patch.shape) * 0.4),
        param1=90,
        param2=22,
        minRadius=int(min(patch.shape) * 0.12),
        maxRadius=int(min(patch.shape) * 0.65),
    )
    if circles is None:
        return 0
    return int(len(circles[0]))


def _seal_from_mask(mask: np.ndarray, image: DocumentImage, method: str) -> dict | None:
    components = _component_stats(mask, min_area=40)
    if not components:
        return None
    min_diameter = max(20, int(min(image.width, image.height) * MIN_SEAL_DIAMETER_RATIO))
    max_diameter = int(min(image.width, image.height) * MAX_SEAL_DIAMETER_RATIO)
    best = None
    for item in components:
        diameter = max(item["w"], item["h"])
        if diameter < min_diameter or diameter > max_diameter:
            continue
        fill = item["area"] / max(item["w"] * item["h"], 1)
        aspect = item["w"] / max(item["h"], 1)
        if fill < SEAL_MIN_FILL or not (0.55 <= aspect <= 1.8):
            continue
        score = min(1.0, item["area"] / 900.0) * (1.0 if fill < 0.4 else 0.5)
        candidate = {
            "method": method,
            "box": (item["x"], item["y"], item["w"], item["h"]),
            "area": item["area"],
            "fill": round(fill, 4),
            "strength": score,
            "plausible": True,
        }
        if best is None or candidate["area"] > best["area"]:
            best = candidate
    if best is None:
        return None
    patch = np.zeros(mask.shape, dtype=bool)
    x, y, w, h = best["box"]
    patch[y : y + h, x : x + w] = mask[y : y + h, x : x + w]
    best["circles"] = _circle_confirmation(patch)
    return best


def _seal_from_blue(image: DocumentImage, zone: Zone) -> dict | None:
    blue = blue_ink_mask(image.rgb)
    mask = np.zeros(blue.shape, dtype=bool)
    mask[zone.y : zone.y2, zone.x : zone.x2] = blue[zone.y : zone.y2, zone.x : zone.x2]
    return _seal_from_mask(mask, image, "colour")


def _seal_from_greyscale(image: DocumentImage, zone: Zone) -> dict | None:
    grey = image.grey
    mask = np.zeros(grey.shape, dtype=bool)
    mask[zone.y : zone.y2, zone.x : zone.x2] = dark_ink_mask(grey)[zone.y : zone.y2, zone.x : zone.x2]
    candidate = _seal_from_mask(mask, image, "greyscale")
    if candidate is None:
        return None
    # Печатные слова кольца не образуют, поэтому требуем либо «кольцевую» долю
    # закраски, либо подтверждение кругами Хафа.
    if candidate["fill"] > 0.62 and candidate["circles"] < 1:
        return None
    return candidate


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------


@dataclass
class MarkVerdict:
    key: str
    present: bool
    confidence: float
    box: tuple[int, int, int, int] | None = None
    method: str = ""
    evidence: dict = field(default_factory=dict)


def detect_marks(
    image: DocumentImage,
    ocr_boxes: OcrResult,
    document_type: str | DocumentTypeSpec,
    spec: DocumentTypeSpec | None = None,
) -> dict[str, MarkVerdict]:
    """Ищем графические подпись и печать для типа документа.

    Цвет используем, когда он есть; серый запасной путь нужен для чёрно-белых
    сканов. OCR тут только ищет зоны по напечатанным словам «Подпись» и «М.П.», а
    не решает, есть ли отметка. Если spec уже загружен, передаём его, чтобы не
    читать конфиг второй раз.
    """
    if spec is None:
        if isinstance(document_type, DocumentTypeSpec):
            spec = document_type
        else:
            from .document_config import get_registry

            spec = get_registry().require(str(document_type))

    rules = find_horizontal_rules(image.grey)
    seal_zone_box = seal_zone(image, ocr_boxes, spec) if spec.has_seal else None
    sig_zone = signature_zone(image, ocr_boxes, spec, seal_zone=seal_zone_box)

    verdicts: dict[str, MarkVerdict] = {}

    # ---------------- подпись ----------------
    signature_evidence: dict = {}
    if sig_zone is not None:
        for collector in (
            lambda: _signature_from_blue(image, sig_zone, seal_zone_box),
            lambda: (
                # В полосе без подписи тоже есть печатный текст, поэтому там считаем
                # только цветные чернила.
                None
                if sig_zone.anchors == ["band"]
                else _signature_from_greyscale(image, ocr_boxes, sig_zone, seal_zone_box, rules)
            ),
        ):
            try:
                evidence = collector()
            except Exception:  # noqa: BLE001 - упавшая эвристика не должна ломать разбор
                evidence = None
            if evidence and evidence.get("plausible"):
                signature_evidence = evidence
                break
    signature_present = bool(signature_evidence)
    signature_conf = round(float(signature_evidence.get("strength", 0.0)), 3) if signature_present else 0.0
    sig_box = signature_evidence.get("box") if signature_evidence else None
    if signature_present and sig_box and seal_zone_box is not None:
        # Печать — отдельная отметка, поэтому между ней и подписью нужен зазор.
        if not (
            sig_box[0] + sig_box[2] <= seal_zone_box.x + 4 or sig_box[0] >= seal_zone_box.x2 - 4
        ):
            overlap_width = min(sig_box[0] + sig_box[2], seal_zone_box.x2) - max(sig_box[0], seal_zone_box.x)
            if overlap_width > 0.5 * sig_box[2]:
                signature_present = False
                signature_conf = 0.0
                sig_box = None

    # ---------------- печать ----------------
    seal_evidence: dict = {}
    if spec.has_seal and seal_zone_box is not None:
        for collector in (
            lambda: _seal_from_blue(image, seal_zone_box),
            lambda: _seal_from_greyscale(image, seal_zone_box),
        ):
            try:
                evidence = collector()
            except Exception:  # noqa: BLE001
                evidence = None
            if evidence and evidence.get("plausible"):
                seal_evidence = evidence
                break
    seal_present = bool(seal_evidence)
    seal_conf = 0.0
    if seal_present:
        base = float(seal_evidence.get("strength", 0.5))
        seal_conf = round(min(1.0, base + 0.15 * seal_evidence.get("circles", 0)), 3)

    for mark in spec.marks:
        if mark.key == "signature_present":
            verdicts[mark.key] = MarkVerdict(
                key=mark.key,
                present=signature_present,
                confidence=signature_conf,
                box=sig_box,
                method=signature_evidence.get("method", ""),
                evidence=signature_evidence,
            )
        elif mark.key == "seal_present":
            verdicts[mark.key] = MarkVerdict(
                key=mark.key,
                present=seal_present,
                confidence=seal_conf,
                box=seal_evidence.get("box"),
                method=seal_evidence.get("method", ""),
                evidence=seal_evidence,
            )
    return verdicts


def detect_marks_dict(
    image: DocumentImage,
    ocr_boxes: OcrResult,
    document_type: str | DocumentTypeSpec,
    spec: DocumentTypeSpec | None = None,
) -> dict[str, bool]:
    """Обёртка для удобства: возвращает только флаги «есть / нет»."""
    verdicts = detect_marks(image, ocr_boxes, document_type, spec=spec)
    return {key: verdict.present for key, verdict in verdicts.items()}
