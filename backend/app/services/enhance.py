"""Бонус «плохие снимки»: делаем из фото с телефона ровный плоский лист.

Шаги идут по порядку, и каждый включается, только если реально нужен (чистый скан
проходит через модуль как есть, так что результаты на открытом наборе не едут):

1. **Обрезка листа** — бумага обычно светлее стола: берём самый большой светлый
   четырёхугольник и растягиваем в прямоугольник, заодно уходят поворот и
   перспектива снимка с рук.
2. **Выравнивание наклона** — направление строк ищем по максимуму дисперсии профиля
   строк на всём круге (лист может лежать и боком) и докручиваем страницу так, чтобы
   строки легли горизонтально. Остаётся вопрос «не вверх ли ногами» — его решает OCR,
   см. ``orientation.py``.
3. **Масштаб** — слишком мелкие и слишком большие фото подгоняем, чтобы у страницы
   были те же ~1600 px по длинной стороне, что у эталонных сканов: тогда пороги
   ниже (высота строки, размер отметки) значат одно и то же на любом входе.
4. **Ровный свет** — делим на сильно размытую оценку фона и так гасим тени, блики и
   цветной свет; делаем это по каждому каналу RGB, чтобы детектор подписи и печати
   всё ещё узнавал синие чернила.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from .loader import DocumentImage

TARGET_LONG_EDGE = 1600
#: Если страница влезает в эти границы — размер не трогаем, оставляем родной.
NATIVE_RANGE = (1200, 2600)


@dataclass
class EnhanceReport:
    steps: list[str] = field(default_factory=list)
    angle: float = 0.0
    upscaled: bool = False

    def note(self, step: str) -> None:
        self.steps.append(step)

    def messages(self) -> list[str]:
        """Пометки для интерфейса человеческим языком: что мы сделали со снимком."""
        texts = {
            "page_crop": "Лист найден на фото и выровнен по краям.",
            "rescale": "Масштаб снимка приведён к стандартному.",
            "flatten_lighting": "Выровнено освещение (тени, блики).",
        }
        out = [texts[step] for step in self.steps if step in texts]
        if "deskew" in self.steps:
            out.append(f"Снимок повёрнут на {abs(self.angle):.1f}° для распознавания.")
        if "upright" in self.steps:
            out.append("Лист был перевёрнут: развернули его для распознавания.")
        return out


def _order_corners(points: np.ndarray) -> np.ndarray:
    points = points.reshape(4, 2).astype(np.float32)
    s = points.sum(axis=1)
    d = np.diff(points, axis=1).ravel()
    return np.array(
        [points[np.argmin(s)], points[np.argmin(d)], points[np.argmax(s)], points[np.argmax(d)]],
        dtype=np.float32,
    )


def find_page_quad(grey: np.ndarray) -> np.ndarray | None:
    """Ищем углы листа, если он выделяется на фоне потемнее."""
    h, w = grey.shape
    factor = 800.0 / max(h, w)
    small = cv2.resize(grey, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (5, 5), 0)
    _, mask = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    total = small.shape[0] * small.shape[1]
    if not (0.2 * total < area < 0.97 * total):
        return None
    # Лист должен быть заметно светлее того, что вокруг, иначе это не наш случай.
    inside = mask > 0
    if small[inside].mean() - small[~inside].mean() < 25:
        return None
    # Подвох: тень тоже делит кадр на светлую и тёмную части. Но у настоящего фото
    # тёмная часть — это стол, а не бумага, и текста там быть не должно.
    text = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15) > 0
    outside = cv2.erode((~inside).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    if outside.any() and inside.any():
        if text[outside].mean() > 0.35 * max(text[inside].mean(), 1e-6):
            return None
    # Край листа — резкий перепад, а граница тени — мягкий градиент, по этому и отличаем.
    edges = cv2.dilate(cv2.Canny(small, 40, 120), np.ones((5, 5), np.uint8)) > 0
    points = contour.reshape(-1, 2)
    sh, sw = small.shape
    inner = points[(points[:, 0] > 4) & (points[:, 0] < sw - 5) & (points[:, 1] > 4) & (points[:, 1] < sh - 5)]
    if len(inner) >= 4 and edges[inner[:, 1], inner[:, 0]].mean() < 0.6:
        return None
    hull = cv2.convexHull(contour)
    quad = None
    for eps in (0.02, 0.03, 0.05, 0.08):
        approx = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            quad = approx
            break
    if quad is None:
        rect = cv2.minAreaRect(hull)
        quad = cv2.boxPoints(rect)
    return _order_corners(np.asarray(quad, dtype=np.float32) / factor)


def warp_page(rgb: np.ndarray, quad: np.ndarray) -> np.ndarray:
    tl, tr, br, bl = quad
    width = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    height = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad, target)
    page = cv2.warpPerspective(rgb, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    # Срезаем узенькую полоску: найденный контур идёт по краю листа и его тени, а OCR
    # радостно читает это как «|».
    inset_x, inset_y = int(width * 0.012), int(height * 0.008)
    return page[inset_y : height - inset_y, inset_x : width - inset_x]


def _text_mask(grey: np.ndarray, long_edge: int = 900) -> np.ndarray:
    factor = long_edge / float(max(grey.shape))
    small = cv2.resize(grey, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    mask = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    # Выкидываем рамки страницы и линии таблиц: длинные компоненты — точно не текст.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    limit = 0.25 * max(small.shape)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_WIDTH] > limit or stats[index, cv2.CC_STAT_HEIGHT] > limit:
            mask[labels == index] = 0
    return mask


def _profile_score(mask: np.ndarray, angle: float) -> float:
    h, w = mask.shape
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(mask, matrix, (w, h), flags=cv2.INTER_NEAREST)
    rows = rotated.sum(axis=1, dtype=np.float64)
    return float(np.var(rows))


def _square(mask: np.ndarray) -> np.ndarray:
    """Кладём маску в квадрат по диагонали: при повороте на любой угол ничего не обрежется."""
    h, w = mask.shape
    side = int(np.ceil(np.hypot(h, w)))
    top, left = (side - h) // 2, (side - w) // 2
    return cv2.copyMakeBorder(mask, top, side - h - top, left, side - w - left, cv2.BORDER_CONSTANT, value=0)


def _refine(mask: np.ndarray, best: float) -> tuple[float, float]:
    """Уточняем грубый угол с шагом 0.1°: (угол, оценка профиля)."""
    fine = np.arange(best - 1.0, best + 1.01, 0.1)
    scores = [_profile_score(mask, a) for a in fine]
    index = int(np.argmax(scores))
    return float(fine[index]), float(scores[index])


def _best_angle(mask: np.ndarray, coarse: np.ndarray) -> tuple[float, float]:
    scores = [_profile_score(mask, a) for a in coarse]
    return _refine(mask, float(coarse[int(np.argmax(scores))]))


def estimate_skew(grey: np.ndarray, limit: float = 35.0) -> float:
    """Угол в градусах против часовой стрелки, при котором строки ложатся ровненько."""
    mask = _text_mask(grey)
    if mask.sum() == 0:
        return 0.0
    return _best_angle(mask, np.arange(-limit, limit + 0.01, 1.0))[0]


#: Насколько профиль строк при большом повороте должен быть лучше, чем при малом,
#: чтобы мы поверили, что лист лежит боком. Запас нужен, чтобы ровный скан с таблицей
#: или колонкой цифр никогда не развернуло на 90°.
SIDEWAYS_MARGIN = 1.5
#: Если уже грубый перебор большого диапазона не дотягивает до этой доли нужного,
#: не уточняем его. На ровных листах (открытый набор и его плохие копии) доля не выше
#: 0.3, на листах боком — от 1.6.
SIDEWAYS_HOPELESS = 0.5


def estimate_text_angle(grey: np.ndarray, small_limit: float = 35.0) -> float:
    """Как ``estimate_skew``, но на всём полукруге: строки могут идти хоть вертикально.

    Возвращает угол в (-90°, 90°]. Отличить «ровно» от «вверх ногами» по одним строкам
    нельзя — это уже дело ``orientation.ocr_upright``.
    """
    raw = _text_mask(grey)
    if raw.sum() == 0:
        return 0.0
    # малый диапазон ищем ровно как ``estimate_skew``: обычный снимок с наклоном до 35°
    # выравнивается так же, как раньше, до пикселя
    small_angle = _best_angle(raw, np.arange(-small_limit, small_limit + 0.01, 1.0))[0]
    # у границы малого диапазона (35–37°) честно берём лучший из двух
    need = 1.0 if abs(small_angle) >= small_limit - 1.0 else SIDEWAYS_MARGIN
    # большой — по маске в квадрате, чтобы при повороте боком ничего не обрезалось;
    # грубый перебор по маске вдвое меньше, иначе это ещё 110 поворотов полной маски
    mask = _square(raw)
    half = cv2.resize(mask, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    wide = np.concatenate([np.arange(-90.0, -small_limit + 1.0, 1.0), np.arange(small_limit, 90.01, 1.0)])
    coarse = [_profile_score(half, a) for a in wide]
    if max(coarse) < SIDEWAYS_HOPELESS * need * _profile_score(half, small_angle):
        return small_angle
    angle, score = _refine(mask, float(wide[int(np.argmax(coarse))]))
    if score > need * _profile_score(mask, small_angle):
        if angle > 90.0:
            angle -= 180.0
        elif angle <= -90.0:
            angle += 180.0
        return angle
    return small_angle


def rotate_page(rgb: np.ndarray, angle: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    rad = np.deg2rad(angle)
    new_w = int(w * abs(np.cos(rad)) + h * abs(np.sin(rad)))
    new_h = int(h * abs(np.cos(rad)) + w * abs(np.sin(rad)))
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    matrix[0, 2] += (new_w - w) / 2
    matrix[1, 2] += (new_h - h) / 2
    return cv2.warpAffine(rgb, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def flatten_lighting(rgb: np.ndarray) -> np.ndarray:
    """Делим на медленно плывущий фон — так уходят тени, блики, цветной свет."""
    h, w = rgb.shape[:2]
    factor = 300.0 / max(h, w)
    small = cv2.resize(rgb, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    # Максимальный фильтр стирает текст, медиана сглаживает остаток — и остаётся чистый фон.
    background = cv2.dilate(small, np.ones((7, 7), np.uint8))
    background = cv2.medianBlur(background, 21)
    background = cv2.resize(background, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    flat = rgb.astype(np.float32) / np.maximum(background, 1.0) * 255.0
    return np.clip(flat, 0, 255).astype(np.uint8)


def needs_flattening(grey: np.ndarray) -> bool:
    """Смотрим, гуляет ли яркость бумаги по странице (тень, блик) — тогда надо выравнивать."""
    h, w = grey.shape
    factor = 200.0 / max(h, w)
    small = cv2.resize(grey, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    background = cv2.dilate(small, np.ones((9, 9), np.uint8))
    p5, p95 = np.percentile(background, (5, 95))
    return bool(p95 - p5 > 18 or p95 < 225)


def whiten_background(rgb: np.ndarray) -> np.ndarray:
    """Красим стол и фон вокруг листа в белый.

    Если лист на фото не обрезали, а потом выровняли наклон, в углах остаются тёмные
    клинья стола, и OCR принимает их за чернила. Текст никогда не доходит до края
    картинки, поэтому большие тёмные куски, которые касаются края, считаем фоном.
    """
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    paper = float(np.percentile(grey, 90))
    dark = (grey < paper - 50).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    h, w = grey.shape
    out = rgb.copy()
    for index in range(1, count):
        x, y, cw, ch, area = stats[index]
        touches = x == 0 or y == 0 or x + cw >= w or y + ch >= h
        if touches and area > 0.005 * h * w:
            region = labels == index
            region = cv2.dilate(region.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
            out[region] = 255
    return out


def _with_rgb(image: DocumentImage, rgb: np.ndarray, quality: str) -> DocumentImage:
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    return replace(image, rgb=np.ascontiguousarray(rgb), grey=grey, quality=quality)


def enhance_document(image: DocumentImage) -> tuple[DocumentImage, EnhanceReport]:
    report = EnhanceReport()
    rgb = image.rgb

    quad = find_page_quad(image.grey)
    if quad is not None:
        rgb = warp_page(rgb, quad)
        report.note("page_crop")

    long_edge = max(rgb.shape[:2])
    if not (NATIVE_RANGE[0] <= long_edge <= NATIVE_RANGE[1]):
        factor = TARGET_LONG_EDGE / float(long_edge)
        report.upscaled = factor > 1.25
        interpolation = cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA
        rgb = cv2.resize(rgb, None, fx=factor, fy=factor, interpolation=interpolation)
        report.note("rescale")

    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if needs_flattening(grey):
        rgb = flatten_lighting(rgb)
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        report.note("flatten_lighting")

    angle = estimate_text_angle(grey)
    # Скан, вставленный боком: крутим ровно на 90° без интерполяции, и это всё ещё
    # чистый скан (бинаризация для OCR ему подходит лучше, чем фото-режим).
    clean_turn = not report.steps and abs(abs(angle) - 90.0) < 0.4
    if clean_turn:
        rgb = np.ascontiguousarray(np.rot90(rgb, 1 if angle > 0 else -1))
        report.angle = 90.0 if angle > 0 else -90.0
        report.note("deskew")
        return _with_rgb(image, rgb, image.quality), report
    if abs(angle) >= 0.4:
        rgb = rotate_page(rgb, angle)
        report.angle = round(angle, 1)
        report.note("deskew")

    if report.steps:
        rgb = whiten_background(rgb)

    if not report.steps:
        return image, report
    return _with_rgb(image, rgb, "lowres" if report.upscaled else "photo"), report
