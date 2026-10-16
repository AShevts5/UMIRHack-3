"""«Плохие снимки»: портим страницы набора так же, как их портит телефон.

Берём пакет в формате организаторов (открытый набор или синтетику) и пишем копию,
где каждую страницу повернули, затенили, засветили бликом, уменьшили и/или
пережали в JPEG. Эталон не трогаем: значения полей те же, так что evaluate.py
сразу показывает, сколько точности мы теряем на кривых фотках.

    python -m backend.scripts.make_degraded --src "../online-dean-participants-v1 (2)" \\
        --out reports/degraded --effect mix
    # можно и по одному эффекту: rotate | shadow | glare | lowres | mix
    # turn — лист повёрнут на любой угол (боком, вверх ногами), flip — скан боком
    # или вверх ногами ровно на 90/180/270° без стола
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
from pathlib import Path

import cv2
import numpy as np

EFFECTS = ("rotate", "shadow", "glare", "lowres", "mix", "turn", "flip")


def rotate(img: np.ndarray, rng: random.Random, max_angle: float = 30.0) -> np.ndarray:
    angle = rng.uniform(3.0, max_angle) * rng.choice((-1, 1))
    return rotate_on_table(img, rng, angle)


def turn(img: np.ndarray, rng: random.Random) -> np.ndarray:
    """Лист лежит на столе как попало: боком, вверх ногами или под любым углом."""
    if rng.random() < 0.6:
        angle = rng.choice((90.0, 180.0, -90.0)) + rng.uniform(-25.0, 25.0)
    else:
        angle = rng.uniform(-180.0, 180.0)
    return rotate_on_table(img, rng, angle)


def flip(img: np.ndarray, rng: random.Random) -> np.ndarray:
    """Скан положили в сканер боком или вверх ногами."""
    return np.ascontiguousarray(np.rot90(img, rng.choice((1, 2, 3))))


def rotate_on_table(img: np.ndarray, rng: random.Random, angle: float) -> np.ndarray:
    h, w = img.shape[:2]
    rad = math.radians(angle)
    new_w = int(w * abs(math.cos(rad)) + h * abs(math.sin(rad)))
    new_h = int(h * abs(math.cos(rad)) + w * abs(math.sin(rad)))
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    matrix[0, 2] += (new_w - w) / 2
    matrix[1, 2] += (new_h - h) / 2
    table = tuple(int(c) for c in rng.choice(((92, 74, 58), (180, 180, 175), (40, 40, 45))))
    out = cv2.warpAffine(img, matrix, (new_w, new_h), flags=cv2.INTER_CUBIC, borderValue=table)
    # добавляем чуть-чуть перспективы: телефон ровно над листом никто не держит
    d = 0.03 * min(new_w, new_h)
    src = np.float32([[0, 0], [new_w, 0], [new_w, new_h], [0, new_h]])
    dst = np.float32([[rng.uniform(0, d), rng.uniform(0, d)], [new_w - rng.uniform(0, d), rng.uniform(0, d)],
                      [new_w - rng.uniform(0, d), new_h - rng.uniform(0, d)], [rng.uniform(0, d), new_h - rng.uniform(0, d)]])
    return cv2.warpPerspective(out, cv2.getPerspectiveTransform(src, dst), (new_w, new_h),
                               flags=cv2.INTER_CUBIC, borderValue=table)


def shadow(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    angle = rng.uniform(0, 2 * math.pi)
    proj = (xx * math.cos(angle) + yy * math.sin(angle))
    proj = (proj - proj.min()) / max(proj.max() - proj.min(), 1)
    darkest = rng.uniform(0.35, 0.6)
    edge = rng.uniform(0.3, 0.7)
    factor = np.where(proj < edge, darkest + (1 - darkest) * (proj / edge) ** 0.5, 1.0)
    factor = cv2.GaussianBlur(factor.astype(np.float32), (0, 0), 25)
    warm = np.array([1.0, 0.97, 0.9], dtype=np.float32)  # желтим, как от лампы в комнате (RGB)
    return np.clip(img.astype(np.float32) * factor[..., None] * warm, 0, 255).astype(np.uint8)


def glare(img: np.ndarray, rng: random.Random) -> np.ndarray:
    h, w = img.shape[:2]
    cx, cy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.15, 0.85) * h
    sx, sy = rng.uniform(0.08, 0.2) * w, rng.uniform(0.05, 0.12) * h
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    blob = np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))
    strength = rng.uniform(0.45, 0.7)
    out = img.astype(np.float32) + (255 - img.astype(np.float32)) * blob[..., None] * strength
    return np.clip(out, 0, 255).astype(np.uint8)


def lowres(img: np.ndarray, rng: random.Random) -> np.ndarray:
    factor = rng.uniform(0.45, 0.65)
    small = cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small, (3, 3), 0.6)


def degrade(img: np.ndarray, effect: str, rng: random.Random) -> np.ndarray:
    if effect == "rotate":
        return rotate(img, rng)
    if effect == "shadow":
        return shadow(img, rng)
    if effect == "glare":
        return glare(img, rng)
    if effect == "lowres":
        return lowres(img, rng)
    if effect == "turn":
        return turn(img, rng)
    if effect == "flip":
        return flip(img, rng)
    out = img
    if rng.random() < 0.8:
        out = shadow(out, rng)
    if rng.random() < 0.5:
        out = glare(out, rng)
    out = rotate(out, rng, max_angle=rng.choice((10.0, 20.0, 30.0)))
    if rng.random() < 0.5:
        out = lowres(out, rng)
    noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 4, out.shape)
    return np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", required=True, help="каталог пакета с documents.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--effect", default="mix", choices=EFFECTS)
    parser.add_argument("--seed", type=int, default=31)
    parser.add_argument("--jpeg-quality", type=int, default=70)
    args = parser.parse_args(argv)

    src, out = Path(args.src), Path(args.out)
    (out / "documents").mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    manifest = json.loads((src / "documents.json").read_text(encoding="utf-8"))
    for entry in manifest:
        rgb = cv2.cvtColor(cv2.imread(str(src / entry["image"])), cv2.COLOR_BGR2RGB)
        bad = degrade(rgb, args.effect, rng)
        name = f"documents/{entry['document_id']}.jpg"
        cv2.imwrite(str(out / name), cv2.cvtColor(bad, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
        entry["image"] = name
        entry.pop("pdf", None)
    (out / "documents.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("questions.json", "reference_results.json"):
        shutil.copy(src / name, out / name)
    print(f"{len(manifest)} страниц, эффект {args.effect} -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
