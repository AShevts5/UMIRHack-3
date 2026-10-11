from typing import Annotated

from pydantic import BaseModel, Field

from app.schemas.common import AnswerStatus, SourceKind

BBox = Annotated[list[int], Field(min_length=4, max_length=4, examples=[[120, 340, 610, 372]])]


class DocTypeResult(BaseModel):

    id: str = Field(examples=["study_certificate"])
    title: str = Field(examples=["Справка об обучении"])
    auto: bool = Field(description="True — тип определён автоматически, False — выбран пользователем")
    confidence: float | None = Field(default=None, ge=0, le=1, examples=[0.93])


class ImageInfo(BaseModel):

    url: str = Field(examples=["/api/files/a1b2c3/page-1.png"])
    width: int = Field(examples=[1240])
    height: int = Field(examples=[1754])
    rotation_deg: float = Field(default=0, examples=[12.5], description="На сколько градусов выровняли снимок")


class ExtractedField(BaseModel):

    key: str = Field(examples=["full_name"])
    label: str = Field(examples=["ФИО студента"])
    value: str = Field(examples=["Иванов Иван Иванович"], description="Если поле не найдено — «не найдено»")
    found: bool
    confidence: float | None = Field(default=None, ge=0, le=1, examples=[0.88])
    page: int | None = Field(default=None, ge=1, examples=[1])
    bbox: BBox | None = None

# источник ответа
class Source(BaseModel):

    kind: SourceKind
    point: str | None = Field(default=None, examples=["3.4"], description="Номер пункта, если kind=regulation")
    quote: str | None = Field(default=None, description="Цитата из пункта, если kind=regulation")
    key: str | None = Field(default=None, examples=["issue_date"], description="Ключ поля, если kind=field")


class Answer(BaseModel):

    status: AnswerStatus
    text: str = Field(
        examples=[
            "Не хватает даты выдачи и печати вуза. Эти поля обязательны по п. 3.4 Положения. "
            "Окончательное решение примет сотрудник деканата."
        ]
    )
    sources: list[Source] = []


class OcrLine(BaseModel):

    text: str
    bbox: BBox
    conf: float = Field(ge=0, le=1)


class OcrResult(BaseModel):

    engine: str = Field(examples=["paddleocr"])
    lines: list[OcrLine] = []


class Timings(BaseModel):

    preprocess: int = 0
    ocr: int = 0
    extract: int = 0
    answer: int = 0


class AnalyzeResponse(BaseModel):

    request_id: str = Field(examples=["a1b2c3"])
    doc_type: DocTypeResult
    image: ImageInfo | None = None
    fields: list[ExtractedField] = []
    answer: Answer
    ocr: OcrResult | None = None
    timings_ms: Timings = Timings()