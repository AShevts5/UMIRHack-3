/** Общие типы API — один в один зеркало backend/app/schemas.py, правим синхронно. */

export interface BoundingBox {
  x: number
  y: number
  width: number
  height: number
}

export interface FieldView {
  key: string
  label: string
  value: string | boolean
  found: boolean
  rule: string
  sources: string[]
  box: BoundingBox | null
  confidence: number
}

export interface MarkView {
  key: string
  label: string
  present: boolean
  confidence: number
  box: BoundingBox | null
  method: string
  evidence: Record<string, unknown>
}

export interface Validation {
  status: 'document_ok' | 'missing_fields'
  missing_fields: string[]
  sources: string[]
}

export interface Answer {
  question_id: string
  answerable: boolean
  value: string | null
  answer: string
  sources: string[]
}

export interface WordBox {
  text: string
  box: BoundingBox
  confidence: number
}

export interface AnalysisResponse {
  document_id: string | null
  document_type: string
  document_type_title: string
  document_type_source: 'request' | 'auto'
  document_type_confidence: number
  ocr_text: string
  ocr_engine: string
  ocr_languages: string
  ocr_mean_confidence: number
  fields: Record<string, string | boolean>
  field_views: FieldView[]
  mark_views: MarkView[]
  validation: Validation
  validation_message: string
  answer: Answer
  boxes: WordBox[]
  warnings: string[]
  /** Приходит от бэкенда, где уже есть /api/v1/ask; у старого этого нет. */
  analysis_id?: string | null
  page_image_url?: string | null
  page_width?: number
  page_height?: number
  /** Страницу подравнивали (поворот, обрезка, свет) — стоит глянуть на исходник для сравнения. */
  page_enhanced?: boolean
  timings_ms?: Record<string, number>
}

export interface DocumentTypeInfo {
  code: string
  title: string
  fields: { key: string; label: string; rule: string }[]
  marks: { key: string; label: string; rule: string }[]
  required_rules: string[]
}

export interface DocumentTypesResponse {
  document_types: DocumentTypeInfo[]
  default_question: string
  limits: {
    max_upload_bytes: number
    max_pdf_pages: number
    allowed_suffixes: string[]
  }
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  ocr: {
    engine: string
    binary: string | null
    available: boolean
    languages: string[]
    version: string | null
    tessdata: string | null
    error?: string
  }
  document_types: Record<string, string>
  version: string
}

/** Кусок Положения из GET /api/regulation. */
export interface RegulationItem {
  point: string
  title: string | null
  text: string
  children: RegulationItem[]
}
