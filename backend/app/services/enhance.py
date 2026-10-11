"""Бонус «плохие снимки»: превращаем фото с телефона в ровный плоский лист.

Шаги идут по порядку, и каждый делается только если он нужен (чистый скан проходит
через модуль без изменений, поэтому результаты на открытом наборе не двигаются):

1. **Обрезка листа** — бумага обычно светлее стола: самый большой светлый
   четырёхугольник растягиваем в прямоугольник, и вместе с этим уходят поворот и
   перспектива ручного снимка.
2. **Выравнивание наклона** — оставшийся угол текста (до ±35°) ищем по максимуму
   дисперсии профиля строк и крутим страницу обратно.
3. **Масштаб** — слишком мелкие и слишком большие фото подгоняем так, чтобы у
   страницы было те же ~1600 px по длинной стороне, что у эталонных сканов: тогда
   пороги ниже (высота строки, размер отметки) значат одно и то же на любом входе.
4. **Ровный свет** — сильно размытая оценка фона делит тени, блики и цветной свет;
   делаем это по каждому каналу RGB, чтобы синие чернила остались узнаваемыми для
   детектора подписи и печати.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from .loader import DocumentImage

TARGET_LONG_EDGE = 1600
#: Страницы в этих границах оставляем в исходном размере.
NATIVE_RANGE = (1200, 2600)


@dataclass
class EnhanceReport:
    steps: list[str] = field(default_factory=list)
    angle: float = 0.0
    upscaled: bool = False

    def note(self, step: str) -> None:
        self.steps.append(step)

    def messages(self) -> list[str]:
        """Понятные человеку пометки для интерфейса: что сделали со снимком."""
        texts = {
            "page_crop": "Лист найден на фото и выровнен по краям.",
            "rescale": "Масштаб снимка приведён к стандартному.",
            "flatten_lighting": "Выровнено освещение (тени, блики).",
        }
        out = [texts[step] for step in self.steps if step in texts]
        if "deskew" in self.steps:
            out.append(f"Снимок повёрнут на {abs(self.angle):.1f}° для распознавания.")
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
    """Углы листа, если он выделяется на более тёмном фоне."""
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
    # Лист должен быть заметно светлее того, что вокруг него.
    inside = mask > 0
    if small[inside].mean() - small[~inside].mean() < 25:
        return None
    # Тень тоже делит кадр на светлую и тёмную части, но тёмная часть настоящего
    # фото — это стол, а не бумага, и текста там быть не должно.
    text = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15) > 0
    outside = cv2.erode((~inside).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    if outside.any() and inside.any():
        if text[outside].mean() > 0.35 * max(text[inside].mean(), 1e-6):
            return None
    # Край листа — резкий перепад, а граница тени — мягкий градиент.
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
    # Срезаем узкую полоску: найденный контур идёт по краю листа и его тени, а OCR
    # читает это как «|».
    inset_x, inset_y = int(width * 0.012), int(height * 0.008)
    return page[inset_y : height - inset_y, inset_x : width - inset_x]


def _text_mask(grey: np.ndarray, long_edge: int = 900) -> np.ndarray:
    factor = long_edge / float(max(grey.shape))
    small = cv2.resize(grey, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    mask = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15)
    # Убираем рамки страницы и линии таблиц: длинные компоненты — это не текст.
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


def estimate_skew(grey: np.ndarray, limit: float = 35.0) -> float:
    """Угол в градусах против часовой стрелки, при котором строки ложатся ровно."""
    mask = _text_mask(grey)
    if mask.sum() == 0:
        return 0.0
    coarse = np.arange(-limit, limit + 0.01, 1.0)
    scores = [_profile_score(mask, a) for a in coarse]
    best = float(coarse[int(np.argmax(scores))])
    fine = np.arange(best - 1.0, best + 1.01, 0.1)
    scores = [_profile_score(mask, a) for a in fine]
    return float(fine[int(np.argmax(scores))])


def rotate_page(rgb: np.ndarray, angle: float) -> np.ndarray:
    h, w = rgb.shape[:2]
    rad = np.deg2rad(abs(angle))
    new_w = int(w * np.cos(rad) + h * np.sin(rad))
    new_h = int(h * np.cos(rad) + w * np.sin(rad))
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    matrix[0, 2] += (new_w - w) / 2
    matrix[1, 2] += (new_h - h) / 2
    return cv2.warpAffine(rgb, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def flatten_lighting(rgb: np.ndarray) -> np.ndarray:
    """Делим на медленно меняющийся фон: тени, блики, цветной свет."""
    h, w = rgb.shape[:2]
    factor = 300.0 / max(h, w)
    small = cv2.resize(rgb, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    # Максимальный фильтр убирает текст, медиана сглаживает остаток — остаётся фон.
    background = cv2.dilate(small, np.ones((7, 7), np.uint8))
    background = cv2.medianBlur(background, 21)
    background = cv2.resize(background, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    flat = rgb.astype(np.float32) / np.maximum(background, 1.0) * 255.0
    return np.clip(flat, 0, 255).astype(np.uint8)


def needs_flattening(grey: np.ndarray) -> bool:
    """Проверяем, гуляет ли яркость бумаги по странице (тень, блик)."""
    h, w = grey.shape
    factor = 200.0 / max(h, w)
    small = cv2.resize(grey, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    background = cv2.dilate(small, np.ones((9, 9), np.uint8))
    p5, p95 = np.percentile(background, (5, 95))
    return bool(p95 - p5 > 18 or p95 < 225)


def whiten_background(rgb: np.ndarray) -> np.ndarray:
    """Закрашиваем стол и фон вокруг листа белым.

    Если у фото не обрезали лист и потом выровняли наклон, в углах остаются тёмные
    клинья стола, а для OCR это похоже на чернила. Текст никогда не доходит до края
    картинки, поэтому большие тёмные области, касающиеся края, считаем фоном.
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

    angle = estimate_skew(grey)
    if abs(angle) >= 0.4:
        rgb = rotate_page(rgb, angle)
        report.angle = round(angle, 1)
        report.note("deskew")

    if report.steps:
        rgb = whiten_background(rgb)

    if not report.steps:
        return image, report
    return _with_rgb(image, rgb, "lowres" if report.upscaled else "photo"), report
