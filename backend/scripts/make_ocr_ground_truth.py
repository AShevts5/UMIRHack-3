"""Build the OCR ground truth for the open case set.

Source of truth
---------------
* boilerplate lines: taken from the shipped page layout (the same text on every page
  of a type) and verified against the page images;
* field lines: the values from the organizer reference file (which this script is
  allowed to read — it is a test-contour tool, see README «Воспроизводимость»);
* dates: the page spelling is not part of the reference, so both accepted spellings
  are stored (``DD.MM.YYYY`` and «1 сентября 2026 года») and the benchmark scores the
  better one, matching the case rule that both must normalize to the same value;
* footer: the sample id is printed on the page, therefore it belongs to the page
  ground truth even though it is not a field.

The generated file is the input of ``benchmark_ocr.py`` and mirrors the role of
``benchmarks/corpus/.../ground_truth.json`` in the PdfOcrDesktop project.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.utils.dates import human_date  # noqa: E402

MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

#: Printed caption of every field, exactly as on the page.
LABEL = {
    "student_name": "ФИО студента",
    "university": "Название вуза",
    "program": "Направление подготовки",
    "study_form": "Форма обучения",
    "study_start": "Начало периода обучения",
    "study_end": "Окончание периода обучения",
    "document_number": "Номер справки",
    "issue_date": "Дата выдачи",
    "group": "Учебная группа",
    "course": "Курс",
    "leave_reason": "Причина отпуска",
    "leave_start": "Дата начала отпуска",
    "leave_end": "Дата окончания отпуска",
    "application_date": "Дата заявления",
    "organization": "Название организации",
    "internship_start": "Дата начала практики",
    "internship_end": "Дата окончания практики",
    "supervisor_name": "ФИО руководителя практики",
    "grade": "Оценка",
}

#: Line order on the page per document type (labels only; values are filled in).
LINE_ORDER = {
    "study_certificate": [
        "student_name", "university", "program", "study_form",
        "study_start", "study_end", "document_number", "issue_date",
    ],
    "academic_leave_application": [
        "student_name", "group", "course", "leave_reason",
        "leave_start", "leave_end", "application_date",
    ],
    "internship_certificate": [
        "student_name", "organization", "internship_start", "internship_end",
        "supervisor_name", "grade", "document_number", "issue_date",
    ],
}

#: Text printed above the fields.
HEADER = {
    "study_certificate": [
        "СИНТЕТИЧЕСКИЙ ДОКУМЕНТ ДЛЯ ХАКАТОНА / НЕ ИМЕЕТ ЮРИДИЧЕСКОЙ СИЛЫ",
        "Учебный документооборот / отдел сопровождения студентов",
        "СПРАВКА ОБ ОБУЧЕНИИ",
        "Сведения об обучении студента приведены ниже.",
    ],
    "academic_leave_application": [
        "СИНТЕТИЧЕСКИЙ ДОКУМЕНТ ДЛЯ ХАКАТОНА / НЕ ИМЕЕТ ЮРИДИЧЕСКОЙ СИЛЫ",
        "В деканат учебного университета «Меридиан»",
        "ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК",
        "Прошу предоставить академический отпуск на указанный период.",
    ],
    "internship_certificate": [
        "СИНТЕТИЧЕСКИЙ ДОКУМЕНТ ДЛЯ ХАКАТОНА / НЕ ИМЕЕТ ЮРИДИЧЕСКОЙ СИЛЫ",
        "Учебный документооборот / отдел сопровождения студентов",
        "СПРАВКА О ПРАКТИКЕ",
        "Сведения о прохождении практики приведены ниже.",
    ],
}

#: Signature caption per type (the mark itself is graphics, not text).
SIGNATURE_CAPTION = {
    "study_certificate": "Подпись сотрудника:",
    "academic_leave_application": "Подпись студента:",
    "internship_certificate": "Подпись руководителя:",
}

FOOTER_TEMPLATE = [
    "Все сведения вымышлены. Подпись и печать являются учебными макетами.",
    "Идентификатор образца: {document_id}",
]

SEAL_CAPTION_TYPES = {"study_certificate", "internship_certificate"}


def _date_variants(iso: str) -> list[str]:
    year, month, day = (int(part) for part in iso.split("-"))
    return [f"{day:02d}.{month:02d}.{year}", human_date(iso)]


def build_ground_truth(archive: Path, out_path: Path) -> dict:
    documents = json.loads((archive / "documents.json").read_text(encoding="utf-8"))
    reference = json.loads((archive / "reference_results.json").read_text(encoding="utf-8"))
    by_id = {item["document_id"]: item for item in reference["documents"]}

    pages = []
    for entry in documents:
        document_id = entry["document_id"]
        kind = entry["document_type"]
        fields = by_id[document_id]["fields"]

        lines: list[str] = list(HEADER[kind])
        field_lines = []
        for key in LINE_ORDER[kind]:
            value = fields[key]
            if value == "не найдено":
                continue
            variants = _date_variants(value) if key.endswith(("_start", "_end", "_date")) or key == "issue_date" else [value]
            field_lines.append({"key": key, "label": LABEL[key], "value": value, "variants": variants})
            lines.append(f"{LABEL[key]}: {variants[0]}")
        lines.append(SIGNATURE_CAPTION[kind])
        if kind in SEAL_CAPTION_TYPES:
            lines.append("УЧЕБНАЯ")
            lines.append("ПЕЧАТЬ")
            lines.append("МАКЕТ")
            lines.append("М.П.")
        lines.extend(line.format(document_id=document_id) for line in FOOTER_TEMPLATE)

        pages.append(
            {
                "document_id": document_id,
                "document_type": kind,
                "image": entry["image"],
                "pdf": entry["pdf"],
                "header_lines": HEADER[kind],
                "field_lines": field_lines,
                "signature_caption": SIGNATURE_CAPTION[kind],
                "footer_lines": [line.format(document_id=document_id) for line in FOOTER_TEMPLATE],
                "page_text": "\n".join(lines),
            }
        )

    payload = {
        "schema_version": 1,
        "source": "open case package online-dean-participants-v1: documents.json + reference_results.json",
        "generated_by": "backend/scripts/make_ocr_ground_truth.py",
        "notes": [
            "Base metric (char_accuracy / exact_page_rate) uses page_text — the full printed text of the page.",
            "Field metric uses field_lines and is reported separately, so recognition and parsing are not mixed.",
            "Dates are accepted in both spellings printed by the case (DD.MM.YYYY and «1 сентября 2026 года»).",
            "The sample-id footer line is part of the page ground truth because it is printed on the page.",
        ],
        "pages": pages,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", required=True, help="каталог открытого набора кейса")
    parser.add_argument("--output", default="benchmarks/ground_truth.json")
    args = parser.parse_args(argv)

    payload = build_ground_truth(Path(args.archive), Path(args.output))
    print(json.dumps({"pages": len(payload["pages"]), "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
