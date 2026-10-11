"""Генератор синтетического стресс-набора в формате пакета организаторов.

Открытый набор мал (30 страниц), а закрытый содержит новые значения и пропуски
при тех же трёх типах и правилах. Скрипт строит N новых документов той же
вёрстки (Liberation Sans, метрически совместимый с Arial), но с другими ФИО,
вузами, организациями, номерами, датами в обоих форматах, случайными пропусками
полей, подписи и печати, и с эталоном, вычисленным напрямую по rules.md.

Результат: каталог с documents.json, questions.json, reference_results.json и
documents/*.png|pdf — его можно прогнать через run_batch и evaluate.py ровно так
же, как открытый набор.

    python -m backend.scripts.make_synthetic --out reports/synthetic --count 150
    python -m backend.scripts.make_synthetic --out reports/synthetic --count 150 --jitter

``--jitter`` слегка сдвигает поля, интервалы, размер шрифта и отметки, чтобы
проверить, что парсер не завязан на точные координаты.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pymupdf

FONT_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
FONT_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

BLANK = "________________________________"
INK = (0x17 / 255, 0x21 / 255, 0x2A / 255)
SIGN_INK = (0.1, 0.23, 0.44)
SEAL_INK = (0.19, 0.34, 0.55)
GREY = (0.6, 0.6, 0.6)

MALE_SURNAMES = (
    "Александров", "Белов", "Воробьёв", "Григорьев", "Дмитриев", "Ершов", "Жуков", "Зайцев",
    "Ильин", "Йодкин", "Ковалёв", "Лебедев", "Морозов", "Никифоров", "Орлов", "Павлов",
    "Рябов", "Семёнов", "Тихонов", "Ульянов", "Фёдоров", "Харитонов", "Цветков", "Чернов",
    "Шестаков", "Щукин", "Эминов", "Юдин", "Яковлев", "Римский-Корсаков", "Мамин-Сибиряк",
)
FEMALE_SURNAMES = (
    "Александрова", "Белова", "Воробьёва", "Григорьева", "Ершова", "Зайцева", "Ильина",
    "Ковалёва", "Лебедева", "Морозова", "Никифорова", "Орлова", "Павлова", "Семёнова",
    "Тихонова", "Фёдорова", "Цветкова", "Чернова", "Шестакова", "Щукина", "Юдина",
    "Яковлева", "Петрова-Водкина",
)
MALE_NAMES = (
    "Артём", "Борис", "Вадим", "Глеб", "Денис", "Егор", "Захар", "Игорь", "Кирилл", "Лев",
    "Матвей", "Никита", "Олег", "Пётр", "Роман", "Семён", "Тимур", "Фёдор", "Юрий", "Ян",
)
FEMALE_NAMES = (
    "Алёна", "Вера", "Галина", "Дарья", "Евгения", "Жанна", "Злата", "Ирина", "Ксения",
    "Любовь", "Мария", "Наталья", "Ольга", "Полина", "Светлана", "Таисия", "Ульяна", "Юлия",
)
PATRONYMICS = (
    ("Алексеевич", "Алексеевна"), ("Андреевич", "Андреевна"), ("Викторович", "Викторовна"),
    ("Дмитриевич", "Дмитриевна"), ("Игоревич", "Игоревна"), ("Николаевич", "Николаевна"),
    ("Сергеевич", "Сергеевна"), ("Петрович", "Петровна"), ("Семёнович", "Семёновна"),
    ("Фёдорович", "Фёдоровна"), ("Юрьевич", "Юрьевна"), ("Ильич", "Ильинична"),
    ("Кузьмич", "Кузьминична"), ("Артёмович", "Артёмовна"),
)

UNIVERSITIES = (
    "Учебный университет «Меридиан»", "Учебная академия «Горизонт»", "Учебный институт «Вектор»",
    "Учебный университет «Северная звезда»", "Учебная академия «Квант»",
    "Учебный институт «Парус-2»", "Учебный колледж «Ёлочка»", "Учебный университет «Атлас»",
)
PROGRAMS = (
    "38.03.01 Экономика", "09.03.01 Информатика и вычислительная техника",
    "01.03.02 Прикладная математика и информатика", "40.03.01 Юриспруденция",
    "42.03.02 Журналистика", "13.03.02 Электроэнергетика и электротехника",
    "45.03.02 Лингвистика", "38.03.02 Менеджмент", "05.03.06 Экология и природопользование",
)
STUDY_FORMS = ("очная", "заочная", "очно-заочная")
ORGANIZATIONS = (
    "Учебная организация «Спектр»", "Учебная организация «Парус»", "Учебная организация «Вектор»",
    "ООО «Учебный Технопарк»", "АО «Учебная Энергосеть»", "Учебная лаборатория «Ёмкость»",
    "Учебное бюро «Север-Юг»", "Учебный центр «Пиксель»",
)
GRADES = ("отлично", "хорошо", "удовлетворительно", "зачтено", "отлично (5)", "хорошо (4)")
REASONS = (
    "по состоянию здоровья", "по семейным обстоятельствам", "в связи с уходом за родственником",
    "в связи с призывом на военную службу", "в связи с рождением ребёнка",
    "в связи с участием в длительной экспедиции", "по личным обстоятельствам",
)
GROUP_PREFIXES = ("ЖР", "ИВТ", "ЭК", "ПМИ", "ЮР", "ЛИН", "МН", "ЭЭ")
NUMBER_PREFIX = {"study_certificate": ("ОБ", "СО", "ОБ/К"), "internship_certificate": ("ПР", "СП", "ПР/У")}

NO_INFO_QUESTIONS = (
    "Какова стоимость обучения за следующий семестр?",
    "Какой размер стипендии положен студенту?",
    "Какой срок рассмотрения документа деканатом?",
    "По какому номеру телефона можно связаться с деканатом?",
    "В каком кабинете принимают оригиналы документов?",
    "Хватит ли этой справки для перевода на 2 курс?",
    "Сколько академических задолженностей у студента?",
    "Какая максимальная длительность академического отпуска?",
    "Сохранится ли стипендия во время отпуска?",
    "Будет ли зачтена практика?",
    "Какие требования к содержанию отчёта по практике?",
    "Подлинная ли подпись на документе?",
    "Где находится деканат?",
    "Какая электронная почта у деканата?",
    "Сколько стоит общежитие?",
    "Когда начинается сессия?",
)

TYPES: dict[str, dict] = {
    "study_certificate": {
        "title": "СПРАВКА ОБ ОБУЧЕНИИ",
        "header": "Учебный документооборот / отдел сопровождения студентов",
        "intro": "Сведения об обучении студента приведены ниже.",
        "fields": (
            ("student_name", "ФИО студента"),
            ("university", "Название вуза"),
            ("program", "Направление подготовки"),
            ("study_form", "Форма обучения"),
            ("study_start", "Начало периода обучения"),
            ("study_end", "Окончание периода обучения"),
            ("document_number", "Номер справки"),
            ("issue_date", "Дата выдачи"),
        ),
        "marks": ("signature_present", "seal_present"),
        "sign_label": "Подпись сотрудника:",
        "text_rule": "rule:3.1",
        "mark_rules": {"signature_present": "rule:3.2", "seal_present": "rule:3.3"},
    },
    "academic_leave_application": {
        "title": "ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК",
        "header": "В деканат учебного университета «Меридиан»",
        "intro": "Прошу предоставить академический отпуск на указанный период.",
        "fields": (
            ("student_name", "ФИО студента"),
            ("group", "Учебная группа"),
            ("course", "Курс"),
            ("leave_reason", "Причина отпуска"),
            ("leave_start", "Дата начала отпуска"),
            ("leave_end", "Дата окончания отпуска"),
            ("application_date", "Дата заявления"),
        ),
        "marks": ("signature_present",),
        "sign_label": "Подпись студента:",
        "text_rule": "rule:4.1",
        "mark_rules": {"signature_present": "rule:4.2"},
    },
    "internship_certificate": {
        "title": "СПРАВКА О ПРАКТИКЕ",
        "header": "Учебный документооборот / отдел сопровождения студентов",
        "intro": "Сведения о прохождении практики приведены ниже.",
        "fields": (
            ("student_name", "ФИО студента"),
            ("organization", "Название организации"),
            ("internship_start", "Дата начала практики"),
            ("internship_end", "Дата окончания практики"),
            ("supervisor_name", "ФИО руководителя практики"),
            ("grade", "Оценка"),
            ("document_number", "Номер справки"),
            ("issue_date", "Дата выдачи"),
        ),
        "marks": ("signature_present", "seal_present"),
        "sign_label": "Подпись руководителя:",
        "text_rule": "rule:5.1",
        "mark_rules": {"signature_present": "rule:5.2", "seal_present": "rule:5.3"},
    },
}


def _first_existing(paths: tuple[str, ...]) -> str:
    for path in paths:
        if Path(path).exists():
            return path
    raise SystemExit("Не найден шрифт Arial/Liberation Sans: " + ", ".join(paths))


def person(rng: random.Random) -> str:
    male, female = rng.choice(PATRONYMICS)
    if rng.random() < 0.5:
        return f"{rng.choice(MALE_SURNAMES)} {rng.choice(MALE_NAMES)} {male}"
    return f"{rng.choice(FEMALE_SURNAMES)} {rng.choice(FEMALE_NAMES)} {female}"


def fmt_date(value: date, rng: random.Random) -> str:
    if rng.random() < 0.5:
        return value.strftime("%d.%m.%Y")
    return f"{value.day} {MONTHS[value.month - 1]} {value.year} года"


def rand_date(rng: random.Random, start: date, span_days: int) -> date:
    return start + timedelta(days=rng.randrange(span_days))


def make_values(doc_type: str, rng: random.Random, index: int) -> tuple[dict, dict]:
    """(нормализованные значения для эталона, напечатанные значения)."""
    expected: dict[str, str] = {}
    printed: dict[str, str] = {}

    def put(key: str, value: str, shown: str | None = None) -> None:
        expected[key] = value
        printed[key] = shown if shown is not None else value

    def put_date(key: str, value: date) -> None:
        put(key, value.isoformat(), fmt_date(value, rng))

    put("student_name", person(rng))
    if doc_type == "study_certificate":
        put("university", rng.choice(UNIVERSITIES))
        put("program", rng.choice(PROGRAMS))
        put("study_form", rng.choice(STUDY_FORMS))
        start = date(rng.randint(2019, 2025), 9, 1)
        put_date("study_start", start)
        put_date("study_end", date(start.year + rng.choice((2, 3, 4, 5)), rng.choice((6, 7, 8)), rng.choice((15, 30))))
        put("document_number", f"{rng.choice(NUMBER_PREFIX[doc_type])}-{rng.randint(1000, 99999)}")
        put_date("issue_date", rand_date(rng, date(2025, 9, 1), 420))
    elif doc_type == "academic_leave_application":
        put("group", f"{rng.choice(GROUP_PREFIXES)}-{rng.randint(19, 26)}-{rng.randint(1, 9)}")
        put("course", str(rng.randint(1, 6)))
        put("leave_reason", rng.choice(REASONS))
        start = rand_date(rng, date(2026, 1, 1), 360)
        put_date("leave_start", start)
        put_date("leave_end", start + timedelta(days=rng.randint(180, 720)))
        put_date("application_date", start - timedelta(days=rng.randint(5, 60)))
    else:
        put("organization", rng.choice(ORGANIZATIONS))
        start = rand_date(rng, date(2025, 5, 1), 420)
        put_date("internship_start", start)
        put_date("internship_end", start + timedelta(days=rng.randint(14, 90)))
        put("supervisor_name", person(rng))
        put("grade", rng.choice(GRADES))
        put("document_number", f"{rng.choice(NUMBER_PREFIX[doc_type])}-{rng.randint(1000, 99999)}")
        put_date("issue_date", start + timedelta(days=rng.randint(95, 130)))
    return expected, printed


def pick_missing(doc_type: str, rng: random.Random, index: int) -> set[str]:
    spec = TYPES[doc_type]
    keys = [key for key, _ in spec["fields"]] + list(spec["marks"])
    roll = index % 5
    if roll == 0:
        return set()
    if roll in (1, 2):
        return {rng.choice(keys)}
    return set(rng.sample(keys, rng.choice((2, 2, 3))))


def draw_signature(page: pymupdf.Page, x: float, y: float, rng: random.Random) -> None:
    shape = page.new_shape()
    points = [pymupdf.Point(x + 10, y - 5)]
    px = x + 10
    for _ in range(rng.randint(9, 14)):
        nx = px + rng.uniform(4, 7)
        c1 = pymupdf.Point(px + 1, y - rng.uniform(8, 18))
        c2 = pymupdf.Point(px + 3, y - rng.uniform(0, 4))
        end = pymupdf.Point(nx, y - rng.uniform(2, 14))
        shape.draw_bezier(points[-1], c1, c2, end)
        points.append(end)
        px = nx
    shape.finish(color=SIGN_INK, width=1.4, closePath=False)
    shape.draw_line(pymupdf.Point(x + 7, y + 1), pymupdf.Point(x + rng.uniform(80, 95), y - rng.uniform(8, 14)))
    shape.finish(color=SIGN_INK, width=1.4)
    shape.commit()


def draw_seal(page: pymupdf.Page, cx: float, cy: float, font: str, bold: str) -> None:
    shape = page.new_shape()
    shape.draw_circle(pymupdf.Point(cx, cy), 41)
    shape.finish(color=SEAL_INK, width=1.5)
    shape.draw_circle(pymupdf.Point(cx, cy), 35)
    shape.finish(color=SEAL_INK, width=1.5)
    shape.commit()
    page.insert_text((cx - 19.5, cy - 11), "УЧЕБНАЯ", fontname="ab", fontfile=bold, fontsize=8, color=SEAL_INK)
    page.insert_text((cx - 16.5, cy), "ПЕЧАТЬ", fontname="ab", fontfile=bold, fontsize=8, color=SEAL_INK)
    page.insert_text((cx - 11.7, cy + 13), "МАКЕТ", fontname="ar", fontfile=font, fontsize=7, color=SEAL_INK)


def render_document(
    doc_id: str, doc_type: str, printed: dict, missing: set[str], rng: random.Random, jitter: bool,
    font: str, bold: str,
) -> pymupdf.Document:
    spec = TYPES[doc_type]
    pdf = pymupdf.open()
    page = pdf.new_page(width=595.2756, height=841.8898)
    left = rng.choice((48.0, 56.0))
    step = 27.525
    size = 11.5
    top = 201.45 if doc_type != "academic_leave_application" else 202.3
    sig_y = 0.0
    if jitter:
        left += rng.uniform(-8, 20)
        step *= rng.uniform(0.9, 1.25)
        size *= rng.uniform(0.9, 1.1)
        top += rng.uniform(-10, 25)
        sig_y = rng.uniform(-30, 25)

    def text(x: float, y: float, value: str, fs: float, color=INK, b: bool = False) -> None:
        page.insert_text((x, y), value, fontname="ab" if b else "ar", fontfile=bold if b else font, fontsize=fs, color=color)

    text(left, 34, "СИНТЕТИЧЕСКИЙ ДОКУМЕНТ ДЛЯ ХАКАТОНА / НЕ ИМЕЕТ ЮРИДИЧЕСКОЙ СИЛЫ", 8, (0x70 / 255,) * 3)
    text(left, 78, spec["header"], 10 if doc_type != "academic_leave_application" else 11)
    text(left, 123.5, spec["title"], 16, b=True)
    text(left, 164.1, spec["intro"], 11)
    for row, (key, label) in enumerate(spec["fields"]):
        value = BLANK if key in missing else printed[key]
        text(left, top + row * step, f"{label}: {value}", size)
    sign_line_x = left + 175
    base = 653.89 + sig_y
    text(left, base, spec["sign_label"], 10)
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(sign_line_x, base - 1.9), pymupdf.Point(sign_line_x + 120, base - 1.9))
    shape.finish(color=GREY, width=0.5)
    shape.commit()
    if "signature_present" in spec["marks"] and "signature_present" not in missing:
        draw_signature(page, sign_line_x, base - 6, rng)
    if "seal_present" in spec["marks"]:
        text(445.28, 708.89 + sig_y, "М.П.", 9)
        if "seal_present" not in missing:
            draw_seal(page, 473.5 + (rng.uniform(-15, 15) if jitter else 0), 652 + sig_y, font, bold)
    text(left, 787.89, "Все сведения вымышлены. Подпись и печать являются учебными макетами.", 8, (0x77 / 255,) * 3)
    text(left, 802.89, f"Идентификатор образца: {doc_id}", 8, (0x77 / 255,) * 3)
    return pdf


def build_reference(doc_id: str, doc_type: str, expected: dict, missing: set[str], questions: list[dict]) -> dict:
    spec = TYPES[doc_type]
    fields: dict = {}
    for key, _ in spec["fields"]:
        fields[key] = "не найдено" if key in missing else expected[key]
    for key in spec["marks"]:
        fields[key] = key not in missing
    missing_keys = [key for key, _ in spec["fields"] if key in missing] + [m for m in spec["marks"] if m in missing]
    if missing_keys:
        sources = []
        if any(key in missing for key, _ in spec["fields"]):
            sources.append(spec["text_rule"])
        sources += [spec["mark_rules"][m] for m in spec["marks"] if m in missing]
        validation = {"status": "missing_fields", "missing_fields": missing_keys, "sources": sources}
    else:
        validation = {
            "status": "document_ok",
            "missing_fields": [],
            "sources": [spec["text_rule"], *spec["mark_rules"].values()],
        }
    answers = []
    for q in questions:
        kind = q.pop("_kind")
        if kind == "completeness":
            answers.append({"question_id": q["question_id"], "answerable": True, "value": validation["status"],
                            "answer": "Результат предварительной проверки.", "sources": validation["sources"]})
        elif kind == "decision":
            answers.append({"question_id": q["question_id"], "answerable": True, "value": "сотрудник деканата",
                            "answer": "Окончательное решение принимает сотрудник деканата.", "sources": ["rule:1.3"]})
        elif kind == "field":
            key = q.pop("_key")
            if key in missing:
                answers.append({"question_id": q["question_id"], "answerable": False, "value": None,
                                "answer": "Поле не заполнено.", "sources": []})
            else:
                answers.append({"question_id": q["question_id"], "answerable": True, "value": expected[key],
                                "answer": "Значение поля.", "sources": [f"field:{key}"]})
        else:
            answers.append({"question_id": q["question_id"], "answerable": False, "value": None,
                            "answer": "Нет информации.", "sources": []})
    return {"document_id": doc_id, "document_type": doc_type, "fields": fields,
            "validation": validation, "answers": answers}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=150)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--jitter", action="store_true", help="сдвигать вёрстку, а не только значения")
    parser.add_argument("--dpi", type=float, default=136.83, help="разрешение PNG (как у открытого набора)")
    args = parser.parse_args(argv)

    font = _first_existing(FONT_REGULAR_CANDIDATES)
    bold = _first_existing(FONT_BOLD_CANDIDATES)
    rng = random.Random(args.seed)
    out = Path(args.out)
    (out / "documents").mkdir(parents=True, exist_ok=True)
    manifest, questions, reference = [], [], []
    types = list(TYPES)
    for i in range(args.count):
        doc_type = types[i % 3]
        doc_id = f"syn-{i + 1:04d}"
        expected, printed = make_values(doc_type, rng, i)
        missing = pick_missing(doc_type, rng, i // 3)
        pdf = render_document(doc_id, doc_type, printed, missing, rng, args.jitter, font, bold)
        pdf.save(out / "documents" / f"{doc_id}.pdf")
        pdf[0].get_pixmap(matrix=pymupdf.Matrix(args.dpi / 72, args.dpi / 72)).save(out / "documents" / f"{doc_id}.png")
        manifest.append({"document_id": doc_id, "document_type": doc_type,
                         "image": f"documents/{doc_id}.png", "pdf": f"documents/{doc_id}.pdf"})
        field_keys = [key for key, _ in TYPES[doc_type]["fields"]]
        # Половина вопросов про поля — про пропущенное поле, как в открытом наборе.
        missing_text = [k for k in field_keys if k in missing]
        key = rng.choice(missing_text) if missing_text and rng.random() < 0.5 else rng.choice(field_keys)
        label = dict(TYPES[doc_type]["fields"])[key]
        doc_qs = [
            {"_kind": "completeness", "question": "Содержит ли документ все обязательные сведения по Положению?"},
            {"_kind": "decision", "question": "Кто принимает окончательное решение о принятии документа?"},
            {"_kind": "field", "_key": key, "question": f"Укажи значение поля «{label}»."},
            {"_kind": "noinfo", "question": rng.choice(NO_INFO_QUESTIONS)},
        ]
        rng.shuffle(doc_qs)
        for n, q in enumerate(doc_qs, 1):
            q["question_id"] = f"{doc_id}-q{n}"
            q["document_id"] = doc_id
        questions.extend({"question_id": q["question_id"], "document_id": doc_id, "question": q["question"]} for q in doc_qs)
        reference.append(build_reference(doc_id, doc_type, expected, missing, doc_qs))

    dump = lambda name, data: (out / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    dump("documents.json", manifest)
    dump("questions.json", questions)
    dump("reference_results.json", {"version": "1.0", "documents": reference})
    print(f"{args.count} документов, {len(questions)} вопросов -> {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
