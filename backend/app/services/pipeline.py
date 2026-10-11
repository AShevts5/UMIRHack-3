"""Single orchestration path used by the web API *and* the batch CLI.

The technical task requires the batch exporter and the interactive flow to use
**exactly the same** OCR/parser/validator functions; this module is that shared
path.  Nothing here reads ``reference_results.json``, file names, or document ids
to derive values: the only inputs are the image pixels and the document type.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from ..schemas import (
    AnalysisResponse,
    Answer,
    BoundingBox,
    FieldView,
    MarkView,
    Validation,
    WordBox,
)
from ..utils.textnorm import norm_text
from . import ocr as ocr_service
from .detect_marks import MarkVerdict, detect_marks
from .document_config import DocumentTypeSpec, get_registry
from .loader import DocumentImage, load_document, load_document_bytes
from .parse_fields import (
    NOT_FOUND,
    ParseResult,
    fields_payload,
    labels_for_missing,
    parse_document,
)
from .enhance import enhance_document
from .refine_fields import refine_fields
from .question_engine import answer_document, build_answer, default_completeness_question
from .rules_engine import STATUS_OK, ValidationResult, clause_list, validate


@dataclass
class PipelineResult:
    document_id: str | None
    document_type: str
    spec: DocumentTypeSpec
    ocr: ocr_service.OcrResult
    parse: ParseResult
    fields: dict[str, str | bool]
    validation: ValidationResult
    marks: dict[str, MarkVerdict]
    document_type_source: str = "request"
    document_type_confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def answers(self, questions: list[tuple[str, str]]) -> list[Answer]:
        return answer_document(self.spec, self.fields, self.validation, questions, self.parse)

    def single_answer(self, question: str, question_id: str = "Q1") -> Answer:
        return build_answer(question_id, question, self.spec, self.fields, self.validation, self.parse)

    def validation_message(self) -> str:
        if self.validation.status == STATUS_OK:
            numbers = clause_list(self.validation.sources)
            return (
                f"Документ содержит все обязательные сведения по пунктам {numbers} Положения. "
                "Окончательное решение примет сотрудник деканата."
            )
        labels = self.validation.missing_labels or self.validation.missing_fields
        numbers = clause_list(self.validation.sources)
        return (
            "Предварительная проверка: не хватает " + ", ".join(labels) + f". "
            f"Требования: пункты {numbers} Положения. Решение принимает сотрудник деканата."
        )


class Pipeline:
    """OCR + parse + marker detection + rules, with a small in-process cache."""

    def __init__(self, cache_size: int = 24, use_cache: bool = True) -> None:
        self.registry = get_registry()
        self._cache: dict[str, PipelineResult] = {}
        self._cache_order: list[str] = []
        self._cache_size = cache_size
        self.use_cache = use_cache
        self.stats = {"documents": 0, "cache_hits": 0, "ocr_seconds": 0.0}

    # -- cache helpers ----------------------------------------------------
    def _cache_key(self, payload: bytes, document_type: str) -> str:
        digest = hashlib.sha256()
        digest.update(document_type.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(payload)
        return digest.hexdigest()

    def _cache_get(self, key: str) -> PipelineResult | None:
        if not self.use_cache:
            return None
        found = self._cache.get(key)
        if found is not None:
            self.stats["cache_hits"] += 1
            return found
        return None

    def _cache_put(self, key: str, value: PipelineResult) -> None:
        if not self.use_cache:
            return
        self._cache[key] = value
        self._cache_order.append(key)
        while len(self._cache_order) > self._cache_size:
            oldest = self._cache_order.pop(0)
            self._cache.pop(oldest, None)

    def clear_cache(self) -> None:
        self._cache.clear()
        self._cache_order.clear()

    # -- main entry points ------------------------------------------------
    def analyze_bytes(
        self,
        payload: bytes,
        filename: str,
        document_type: str = "auto",
        document_id: str | None = None,
    ) -> PipelineResult:
        import time

        registry = self.registry
        if document_type not in (None, "", "auto"):
            spec = registry.require(document_type)
            key = self._cache_key(payload, spec.code)
            cached = self._cache_get(key)
            if cached is not None:
                cached.document_id = document_id
                return cached
            image = load_document_bytes(payload, filename, limits=settings.limits)
            image, enhance_report = enhance_document(image)
            started = time.perf_counter()
            ocr_result = ocr_service.run_ocr(image)
            self.stats["ocr_seconds"] += time.perf_counter() - started
            result = self._process(image, ocr_result, spec, document_id=document_id)
            result.warnings.extend(enhance_report.messages())
            self._cache_put(key, result)
            return result

        # auto-detect (bonus): OCR first, then pick the best matching type
        image = load_document_bytes(payload, filename, limits=settings.limits)
        image, enhance_report = enhance_document(image)
        started = time.perf_counter()
        ocr_result = ocr_service.run_ocr(image)
        self.stats["ocr_seconds"] += time.perf_counter() - started
        code, score = registry.detect_type(ocr_result.text)
        if code is None:
            raise ValueError(
                "Не удалось автоматически определить тип документа. Выберите тип вручную."
            )
        spec = registry.require(code)
        result = self._process(image, ocr_result, spec, document_id=document_id)
        result.warnings.extend(enhance_report.messages())
        result.document_type_source = "auto"
        result.document_type_confidence = score
        return result

    def analyze_path(
        self,
        path: str | Path,
        document_type: str,
        document_id: str | None = None,
    ) -> PipelineResult:
        file_path = Path(path)
        payload = file_path.read_bytes()
        return self.analyze_bytes(payload, file_path.name, document_type, document_id)

    # -- internals --------------------------------------------------------
    def _process(
        self,
        image: DocumentImage,
        ocr_result: ocr_service.OcrResult,
        spec: DocumentTypeSpec,
        document_id: str | None = None,
    ) -> PipelineResult:
        parse_result = parse_document(ocr_result, spec)
        refine_fields(image, ocr_result, spec, parse_result)
        mark_verdicts = detect_marks(image, ocr_result, spec)
        parse_result.mark_results = mark_verdicts
        parse_result.document_type = spec.code
        fields = fields_payload(parse_result, spec)
        validation = validate(spec, fields)
        warnings: list[str] = []
        if ocr_result.mean_conf and ocr_result.mean_conf < 70:
            warnings.append(
                "Средняя уверенность OCR ниже 70%: проверьте распознанный текст перед выводами."
            )
        if not ocr_result.words:
            warnings.append("Текст на изображении не распознан.")
        self.stats["documents"] += 1
        return PipelineResult(
            document_id=document_id,
            document_type=spec.code,
            spec=spec,
            ocr=ocr_result,
            parse=parse_result,
            fields=fields,
            validation=validation,
            marks=mark_verdicts,
            warnings=warnings,
        )


def build_api_response(
    result: PipelineResult,
    question: str | None = None,
    question_id: str = "Q1",
    include_boxes: bool = True,
    max_boxes: int = 1500,
) -> AnalysisResponse:
    """Shape a pipeline result into the ``/api/v1/analyze`` payload."""
    spec = result.spec
    question_text = question or default_completeness_question()
    answer = result.single_answer(question_text, question_id)

    field_views: list[FieldView] = []
    for field_spec in spec.fields:
        field_result = result.parse.field_results.get(field_spec.key)
        value = result.fields.get(field_spec.key, NOT_FOUND)
        box = None
        if field_result and field_result.box and field_result.found:
            x, y, width, height = field_result.box
            box = BoundingBox(x=x, y=y, width=width, height=height)
        field_views.append(
            FieldView(
                key=field_spec.key,
                label=field_spec.label,
                value=value,
                found=bool(field_result and field_result.found),
                rule=spec.rule_for_field(field_spec.key),
                sources=[f"field:{field_spec.key}"],
                box=box,
                confidence=round(field_result.confidence, 3) if field_result else 0.0,
            )
        )

    mark_views: list[MarkView] = []
    for mark_spec in spec.marks:
        verdict = result.marks.get(mark_spec.key)
        box = None
        if verdict and verdict.box:
            x, y, width, height = verdict.box
            box = BoundingBox(x=x, y=y, width=width, height=height)
        mark_views.append(
            MarkView(
                key=mark_spec.key,
                label=mark_spec.label,
                present=bool(verdict and verdict.present),
                confidence=round(verdict.confidence, 3) if verdict else 0.0,
                box=box,
                method=verdict.method if verdict else "",
                evidence=dict(verdict.evidence) if verdict else {},
            )
        )

    boxes: list[WordBox] = []
    if include_boxes:
        for word in result.ocr.words[:max_boxes]:
            boxes.append(
                WordBox(
                    text=word.text,
                    box=BoundingBox(x=word.left, y=word.top, width=word.width, height=word.height),
                    confidence=round(word.conf, 1),
                )
            )

    return AnalysisResponse(
        document_id=result.document_id,
        document_type=result.document_type,
        document_type_title=spec.title,
        document_type_source=result.document_type_source,  # type: ignore[arg-type]
        document_type_confidence=result.document_type_confidence,
        ocr_text=result.ocr.text,
        ocr_engine=result.ocr.engine,
        ocr_languages=ocr_service.resolve_languages(settings.ocr.languages),
        ocr_mean_confidence=result.ocr.mean_conf,
        fields=result.fields,
        field_views=field_views,
        mark_views=mark_views,
        validation=Validation(**result.validation.as_dict()),
        validation_message=result.validation_message(),
        answer=answer,
        boxes=boxes,
        warnings=result.warnings,
    )


def missing_labels(spec: DocumentTypeSpec, keys: list[str]) -> list[str]:
    return labels_for_missing(spec, keys)


def normalized(text: str) -> str:
    return norm_text(text)


#: Module-level pipeline instance shared by API and CLI (models/engines are not
#: reloaded per request; OCR runs once per distinct document).
pipeline = Pipeline()


def analyze_file(path: str | Path, document_type: str, document_id: str | None = None) -> PipelineResult:
    return pipeline.analyze_path(path, document_type, document_id)
