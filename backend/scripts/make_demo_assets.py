"""Демо-примеры и цифры для слайдов — всё из реальных прогонов.

Кладём в ``reports/demo/``:

* ``demo.json`` — самые показательные случаи (три типа документов, пропущенное поле, случаи
  с отметками, честный отказ, ответ про окончательное решение) с живым выводом
  pipeline;
* ``api_examples.json`` — те же случаи, как их отдаёт ``POST /api/v1/analyze``
  (без боксов слов, а то файл распухнет);
* ``summary.md`` — табличка покороче, для слайдов.

Из архива берём только документы, в эталон не лезем вообще, так что всё, что тут
печатается, — честный вывод модели, без подглядывания.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..app.services.pipeline import Pipeline, build_api_response
from .run_batch import resolve_document_path

#: (document_id, тип документа, вопрос, что этим случаем хотим показать)
CASES = [
    ("open-001", "study_certificate", None, "Полная справка об обучении: все обязательные сведения и обе отметки"),
    ("open-002", "study_certificate", "Укажи значение поля «Дата выдачи».", "Поле отсутствует: честный отказ вместо значения"),
    ("open-003", "study_certificate", None, "Пустая линия подписи: подпись не обнаружена, печать есть"),
    ("open-004", "study_certificate", None, "Есть подпись, нет печати (одинокое «М.П.» не считается печатью)"),
    ("open-010", "study_certificate", None, "Нет ни подписи, ни печати: оба источника rule:3.2 и rule:3.3"),
    ("open-014", "academic_leave_application", None, "Заявление с пропуском: не хватает причины отпуска (rule:4.1)"),
    ("open-018", "academic_leave_application", "Кто принимает окончательное решение о принятии документа?", "Ответ по Положению: сотрудник деканата, rule:1.3"),
    ("open-021", "internship_certificate", None, "Справка о практике: графическая подпись и печать обнаружены"),
    ("open-023", "internship_certificate", None, "Печать есть, подписи нет (rule:5.2)"),
    ("open-004", "study_certificate", "Какова стоимость обучения за следующий семестр?", "Нет данных в документе и Положении"),
]


def build(root: Path, out_dir: Path, prefer: str = "image") -> dict:
    documents = json.loads((root / "documents.json").read_text(encoding="utf-8"))
    by_id = {item["document_id"]: item for item in documents}
    pipeline = Pipeline(use_cache=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    demo: list[dict] = []
    api_examples: list[dict] = []
    seen: set[tuple[str, str | None]] = set()

    for document_id, document_type, question, purpose in CASES:
        key = (document_id, question)
        if key in seen:  # pragma: no cover - чисто на всякий случай
            continue
        seen.add(key)
        entry = by_id[document_id]
        path = resolve_document_path(entry, root, prefer=prefer)
        result = pipeline.analyze_path(path, document_type, document_id)
        answer = result.single_answer(question or "Содержит ли документ все обязательные сведения?", f"{document_id}-demo")
        api_payload = build_api_response(result, question=question, question_id=f"{document_id}-demo", include_boxes=False)

        record = {
            "document_id": document_id,
            "document_type": document_type,
            "purpose": purpose,
            "file": entry[prefer],
            "question": question or "Содержит ли документ все обязательные сведения по Положению?",
            "fields": result.fields,
            "validation": result.validation.as_dict(),
            "validation_message": result.validation_message(),
            "answer": answer.model_dump(),
            "marks": {
                key: {
                    "present": verdict.present,
                    "method": verdict.method,
                    "confidence": verdict.confidence,
                    "box": verdict.box,
                }
                for key, verdict in result.marks.items()
            },
            "ocr_mean_confidence": result.ocr.mean_conf,
            "ocr_excerpt": "\n".join(result.ocr.text.splitlines()[:6]),
        }
        demo.append(record)
        api_examples.append({"purpose": purpose, "request": {"document_type": document_type, "question": record["question"], "file": entry[prefer]}, "response": api_payload.model_dump()})

    (out_dir / "demo.json").write_text(json.dumps(demo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "api_examples.json").write_text(
        json.dumps(api_examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = ["# Демонстрационные случаи (реальный вывод pipeline)", ""]
    lines.append("| Документ | Тип | Что показывает | Статус | Пропуски | Ответ |")
    lines.append("|---|---|---|---|---|---|")
    for record in demo:
        missing = ", ".join(record["validation"]["missing_fields"]) or "—"
        lines.append(
            f"| `{record['document_id']}` | `{record['document_type']}` | {record['purpose']} | "
            f"`{record['validation']['status']}` | {missing} | "
            f"`{record['answer']['value']}` / {record['answer']['sources'] or '[]'} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"cases": len(demo), "out_dir": str(out_dir)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, help="каталог архива")
    parser.add_argument("--out", default="reports/demo", help="куда писать материалы")
    parser.add_argument("--prefer", default="image", choices=["image", "pdf"])
    args = parser.parse_args(argv)
    summary = build(Path(args.root), Path(args.out), args.prefer)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
