from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

class AnswerStatus(str, Enum):

    ok = "ok"
    missing = "missing"  # чего-то не хватает
    no_info = "no_info"  # в документе и положении нет ответа

# источник ответа
class SourceKind(str, Enum):

    regulation = "regulation"  # пункт Положения
    field = "field"            # поле документа


class TwoFactorMethod(str, Enum):

    email = "email"
    totp = "totp"


class MessageResponse(BaseModel):

    message: str = Field(examples=["Код отправлен на почту"])


class ErrorResponse(BaseModel):

    detail: str = Field(examples=["Файл должен быть в формате JPG, PNG или PDF"])




class DocTypeField(BaseModel):

    key: str = Field(examples=["full_name"])
    label: str = Field(examples=["ФИО студента"])
    type: str = Field(examples=["person_name"], description="date, person_name, text, number, period, grade, presence")


class DocTypeInfo(BaseModel):

    id: str = Field(examples=["study_certificate"])
    title: str = Field(examples=["Справка об обучении"])
    fields: list[DocTypeField] = []


class RegulationPoint(BaseModel):

    point: str = Field(examples=["3.4"])
    title: str | None = Field(default=None, examples=["Требования к справке"])
    text: str = Field(examples=["Справка должна содержать ФИО студента, дату выдачи ..."])
    children: list["RegulationPoint"] = []


RegulationPoint.model_rebuild()


class HistoryItem(BaseModel):

    request_id: str = Field(examples=["a1b2c3"])
    created_at: datetime
    filename: str = Field(examples=["spravka.jpg"])
    doc_type_title: str | None = Field(default=None, examples=["Справка об обучении"])
    question: str = Field(examples=["Хватит ли справки для перевода на 2 курс?"])
    status: AnswerStatus