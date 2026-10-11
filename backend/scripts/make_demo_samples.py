"""Готовые образцы для демо-режима и для примера четвёртого типа документа.

Рисуем документы тем же генератором, что и синтетический стресс-набор
(``make_synthetic``): вёрстка та же, значения выдуманные, подпись и печать —
учебные макеты. Документы из открытого набора организаторов сюда не тащим.

Что на выходе:

* ``frontend/public/demo/`` — образцы для кнопок демо-режима: полная справка
  об обучении (PNG), заявление на академический отпуск без подписи и даты
  заявления (PNG), полная справка о практике (PDF) и «фото под углом» —
  справка об обучении, повёрнутая и в тени, будто сняли на телефон (JPG);
* ``examples/new-document-type/work-certificate.png`` — справка с места работы,
  чтобы показать тип, который добавлен одним только конфигом;
* ``examples/new-document-type/rules.md`` — копия ``rules.md`` с
  демонстрационным разделом 7 под этот тип.

    python -m backend.scripts.make_demo_samples
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from . import make_degraded as bad
from . import make_synthetic as syn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DPI = 136.83

WORK_TYPE = {
    "title": "СПРАВКА С МЕСТА РАБОТЫ",
    "header": "Отдел кадров / учебный пример",
    "intro": "Настоящая справка выдана для предъявления в деканат.",
    "fields": (
        ("employee_name", "ФИО работника"),
        ("organization", "Название организации"),
        ("position", "Должность"),
        ("employment_start", "Дата приёма на работу"),
        ("document_number", "Номер справки"),
        ("issue_date", "Дата выдачи"),
    ),
    "marks": ("signature_present", "seal_present"),
    "sign_label": "Подпись руководителя:",
}

WORK_PRINTED = {
    "employee_name": "Лебедева Анна Сергеевна",
    "organization": "ООО «Учебный пример»",
    "position": "младший аналитик",
    "employment_start": "1 июля 2026 г.",
    "document_number": "РБ-20417",
    "issue_date": "5 октября 2026 г.",
}

RULES_APPENDIX = """
## 7. Справка с места работы (демонстрация, не входит в Положение кейса)
Этот раздел добавлен только в пример `examples/new-document-type`, чтобы показать тип документа, описанный одним конфигом. В Положении кейса его нет.
7.1. Обязательные текстовые поля: ФИО работника, название организации, должность, дата приёма на работу, номер справки, дата выдачи.
7.2. Обязательна подпись руководителя организации.
7.3. Обязательна печать организации.
7.4. При отсутствии любого элемента из пунктов 7.1-7.3 сервис перечисляет недостающие поля и указывает соответствующие пункты.
"""

# (имя файла, тип, что пропущено, формат) — порядок не путать
DEMO_SAMPLES = (
    ("study-certificate", "study_certificate", set(), "png"),
    ("academic-leave", "academic_leave_application", {"signature_present", "application_date"}, "png"),
    ("internship-certificate", "internship_certificate", set(), "pdf"),
)


def _save(pdf: pymupdf.Document, target: Path, fmt: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "pdf":
        pdf.subset_fonts()
        pdf.save(target, garbage=4, deflate=True)
    else:
        pdf[0].get_pixmap(matrix=pymupdf.Matrix(DPI / 72, DPI / 72)).save(target)


def build(root: Path = PROJECT_ROOT, seed: int = 11) -> list[Path]:
    font = syn._first_existing(syn.FONT_REGULAR_CANDIDATES)
    bold = syn._first_existing(syn.FONT_BOLD_CANDIDATES)
    rng = random.Random(seed)
    written: list[Path] = []
    for index, (name, doc_type, missing, fmt) in enumerate(DEMO_SAMPLES):
        _, printed = syn.make_values(doc_type, rng, index)
        pdf = syn.render_document(f"demo-{index + 1}", doc_type, printed, missing, rng, False, font, bold)
        target = root / "frontend" / "public" / "demo" / f"{name}.{fmt}"
        _save(pdf, target, fmt)
        written.append(target)

    # «Фото под углом»: вёрстка та же, но лист повёрнут и в тени, будто сняли на телефон.
    photo_rng = random.Random(3)
    _, printed = syn.make_values("study_certificate", photo_rng, 7)
    pdf = syn.render_document("demo-photo", "study_certificate", printed, set(), photo_rng, False, font, bold)
    pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(DPI / 72, DPI / 72))
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3]
    photo = bad.rotate(bad.shadow(rgb, photo_rng), photo_rng, max_angle=14.0)
    target = root / "frontend" / "public" / "demo" / "photo-angle.jpg"
    cv2.imwrite(str(target), cv2.cvtColor(photo, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
    written.append(target)

    example = root / "examples" / "new-document-type"
    syn.TYPES["work_certificate"] = WORK_TYPE
    try:
        pdf = syn.render_document("demo-work", "work_certificate", WORK_PRINTED, set(), rng, False, font, bold)
    finally:
        del syn.TYPES["work_certificate"]
    _save(pdf, example / "work-certificate.png", "png")
    written.append(example / "work-certificate.png")

    rules = (root / "rules.md").read_text(encoding="utf-8").rstrip("\n") + "\n" + RULES_APPENDIX
    (example / "rules.md").write_text(rules, encoding="utf-8")
    written.append(example / "rules.md")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(PROJECT_ROOT))
    args = parser.parse_args(argv)
    for path in build(Path(args.root)):
        print(path.relative_to(Path(args.root)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
