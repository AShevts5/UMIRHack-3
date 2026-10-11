# временные моки для фронта

from datetime import datetime, timedelta, timezone
from html import escape

from app.schemas import (
    AnalyzeResponse,
    Answer,
    AnswerStatus,
    DocTypeField,
    DocTypeInfo,
    DocTypeResult,
    ExtractedField,
    HistoryItem,
    ImageInfo,
    RegulationPoint,
    Source,
    SourceKind,
    Timings,
)

NOT_FOUND = "не найдено"

# типы документов

DOC_TYPES: list[DocTypeInfo] = [
    DocTypeInfo(
        id="study_certificate",
        title="Справка об обучении",
        fields=[
            DocTypeField(key="full_name", label="ФИО студента", type="person_name"),
            DocTypeField(key="university", label="Учебное заведение", type="text"),
            DocTypeField(key="study_period", label="Период обучения", type="period"),
            DocTypeField(key="issue_number", label="Номер справки", type="text"),
            DocTypeField(key="issue_date", label="Дата выдачи", type="date"),
            DocTypeField(key="seal", label="Печать", type="presence"),
        ],
    ),
    DocTypeInfo(
        id="academic_leave",
        title="Заявление на академический отпуск",
        fields=[
            DocTypeField(key="full_name", label="ФИО студента", type="person_name"),
            DocTypeField(key="group", label="Группа", type="text"),
            DocTypeField(key="leave_start", label="Дата начала отпуска", type="date"),
            DocTypeField(key="leave_reason", label="Основание", type="text"),
            DocTypeField(key="signature", label="Подпись", type="presence"),
        ],
    ),
    DocTypeInfo(
        id="practice_certificate",
        title="Справка о прохождении практики",
        fields=[
            DocTypeField(key="full_name", label="ФИО студента", type="person_name"),
            DocTypeField(key="organization", label="Организация", type="text"),
            DocTypeField(key="practice_period", label="Период практики", type="period"),
            DocTypeField(key="grade", label="Оценка", type="grade"),
            DocTypeField(key="seal", label="Печать", type="presence"),
        ],
    ),
]

DOC_TYPE_TITLES: dict[str, str] = {d.id: d.title for d in DOC_TYPES}



REGULATION: list[RegulationPoint] = [
    RegulationPoint(
        point="3",
        title="Требования к документам ",
        text="Тестовый текст раздела. Будет заменён настоящим текстом Положения.",
        children=[
            RegulationPoint(
                point="3.4",
                title="Требования к справке об обучении ",
                text="Тестовый текст пункта: справка должна содержать ФИО студента, "
                "название учебного заведения, период обучения, номер, дату выдачи и печать.",
            ),
            RegulationPoint(
                point="3.5",
                title="Требования к справке о практике ",
                text="Тестовый текст пункта о справке с места практики.",
            ),
        ],
    ),
    RegulationPoint(
        point="4",
        title="Академический отпуск ",
        text="Тестовый текст раздела об академическом отпуске.",
        children=[
            RegulationPoint(
                point="4.2",
                title="Заявление на академический отпуск",
                text="Тестовый текст пункта о заявлении и его обязательных полях.",
            ),
        ],
    ),
]


def find_regulation_point(point: str, items: list[RegulationPoint] | None = None) -> RegulationPoint | None:

    for item in REGULATION if items is None else items:
        if item.point == point:
            return item
        found = find_regulation_point(point, item.children)
        if found is not None:
            return found
    return None



IMAGE_WIDTH, IMAGE_HEIGHT = 1240, 1754

# Поля справки об обучении: (ключ, подпись, значение, рамка на снимке)
_STUDY_FIELDS: list[tuple[str, str, str, list[int]]] = [
    ("full_name", "ФИО студента", "Иванов Иван Иванович", [120, 340, 610, 372]),
    ("university", "Учебное заведение", "РГЭУ (РИНХ)", [120, 400, 900, 432]),
    ("study_period", "Период обучения", "01.09.2025 — 30.06.2029", [120, 460, 760, 492]),
    ("issue_number", "Номер справки", "№ 1234", [120, 520, 420, 552]),
    ("issue_date", "Дата выдачи", "15.09.2026", [700, 520, 1000, 552]),
    ("seal", "Печать", "есть", [800, 1400, 1050, 1650]),
]

# Какие поля «не найдены» в варианте missing
_MISSING_KEYS = {"issue_date", "seal"}


def pick_mock_status(question: str) -> AnswerStatus:

    q = question.lower()
    if "погод" in q or "столов" in q:
        return AnswerStatus.no_info
    if "хват" in q or "подход" in q or "достаточ" in q:
        return AnswerStatus.missing
    return AnswerStatus.ok


def _build_fields(status: AnswerStatus) -> list[ExtractedField]:
    fields = []
    for key, label, value, bbox in _STUDY_FIELDS:
        missing = status == AnswerStatus.missing and key in _MISSING_KEYS
        fields.append(
            ExtractedField(
                key=key,
                label=label,
                value=NOT_FOUND if missing else value,
                found=not missing,
                confidence=None if missing else 0.9,
                page=None if missing else 1,
                bbox=None if missing else bbox,
            )
        )
    return fields


def _build_answer(status: AnswerStatus) -> Answer:
    if status == AnswerStatus.ok:
        return Answer(
            status=status,
            text="Справка содержит все обязательные поля по п. 3.4 Положения. "
            "Окончательное решение примет сотрудник деканата.",
            sources=[Source(kind=SourceKind.regulation, point="3.4", quote="Тестовая цитата пункта 3.4")],
        )
    if status == AnswerStatus.missing:
        return Answer(
            status=status,
            text="Не хватает даты выдачи и печати вуза. Эти поля обязательны по п. 3.4 Положения. "
            "Окончательное решение примет сотрудник деканата.",
            sources=[
                Source(kind=SourceKind.regulation, point="3.4", quote="Тестовая цитата пункта 3.4"),
                Source(kind=SourceKind.field, key="issue_date"),
                Source(kind=SourceKind.field, key="seal"),
            ],
        )
    return Answer(
        status=status,
        text="В документе и в Положении нет информации по этому вопросу. Обратитесь в деканат.",
        sources=[],
    )


def build_analyze_response(
    request_id: str,
    doc_type_id: str | None,
    status: AnswerStatus,
) -> AnalyzeResponse:

    auto = doc_type_id is None
    type_id = doc_type_id or "study_certificate"
    return AnalyzeResponse(
        request_id=request_id,
        doc_type=DocTypeResult(
            id=type_id,
            title=DOC_TYPE_TITLES[type_id],
            auto=auto,
            confidence=0.93 if auto else None,
        ),
        image=ImageInfo(
            url=f"/api/files/{request_id}/page-1",
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
            rotation_deg=0,
        ),
        fields=_build_fields(status),
        answer=_build_answer(status),
        ocr=None,
        timings_ms=Timings(preprocess=150, ocr=1800, extract=250, answer=1200),
    )


def placeholder_page_svg() -> str:

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{IMAGE_WIDTH}" height="{IMAGE_HEIGHT}" '
        f'viewBox="0 0 {IMAGE_WIDTH} {IMAGE_HEIGHT}">',
        f'<rect width="{IMAGE_WIDTH}" height="{IMAGE_HEIGHT}" fill="#ffffff"/>',
        '<text x="120" y="200" font-family="Arial" font-size="48" fill="#999999">'
        "ОБРАЗЕЦ — ЗАГЛУШКА</text>",
    ]
    for _key, label, value, (x1, y1, x2, y2) in _STUDY_FIELDS:
        parts.append(
            f'<rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}" '
            'fill="none" stroke="#cccccc" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x1 + 8}" y="{y2 - 8}" font-family="Arial" font-size="22" fill="#333333">'
            f"{escape(label)}: {escape(value)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)



_now = datetime.now(timezone.utc)

HISTORY: list[HistoryItem] = [
    HistoryItem(
        request_id="demo00000002",
        created_at=_now - timedelta(hours=1),
        filename="spravka_praktika.pdf",
        doc_type_title="Справка о прохождении практики",
        question="Подходит ли справка для зачёта практики?",
        status=AnswerStatus.missing,
    ),
    HistoryItem(
        request_id="demo00000001",
        created_at=_now - timedelta(days=1),
        filename="spravka.jpg",
        doc_type_title="Справка об обучении",
        question="Какая дата выдачи у справки?",
        status=AnswerStatus.ok,
    ),
]


def add_history(response: AnalyzeResponse, filename: str, question: str) -> None:

    HISTORY.insert(
        0,
        HistoryItem(
            request_id=response.request_id,
            created_at=datetime.now(timezone.utc),
            filename=filename,
            doc_type_title=response.doc_type.title,
            question=question,
            status=response.answer.status,
        ),
    )