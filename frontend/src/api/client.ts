import { award } from '../lib/badges'
import type { AnalysisResponse, Answer, DocumentTypesResponse, HealthResponse, RegulationItem } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'
// Положение живёт по общему адресу /api/regulation (без версии, не путать с v1).
const ROOT = BASE.replace(/\/v1\/?$/, '')

/** Ошибка API с HTTP-статусом: по статусу интерфейс решает, что написать и что делать. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function readError(response: Response): Promise<ApiError> {
  let message = `Ошибка ${response.status}`
  try {
    const payload = await response.json()
    if (typeof payload?.detail === 'string') message = payload.detail
    else if (Array.isArray(payload?.detail)) {
      message = payload.detail.map((item: { msg?: string }) => item.msg ?? 'ошибка').join('; ')
    }
  } catch {
    message = `Ошибка ${response.status}: ${response.statusText}`
  }
  return new ApiError(message, response.status)
}

/** Кто вошёл и какой вуз выбран: если есть, проверка улетит в «Историю обращений». */
function sessionHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  try {
    const token = localStorage.getItem('umir-token')
    const university = localStorage.getItem('umir-university')
    if (token) headers.Authorization = `Bearer ${token}`
    if (university) headers['X-University'] = university
  } catch {
    // нет localStorage — ну и ладно, просто не ведём историю
  }
  return headers
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) throw await readError(response)
  return response.json()
}

export function fetchDocumentTypes(): Promise<DocumentTypesResponse> {
  return getJson(`${BASE}/document-types`)
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson(`${BASE}/health`)
}

export async function fetchRegulation(): Promise<RegulationItem[]> {
  const payload = await getJson<{ items: RegulationItem[] }>(`${ROOT}/regulation`)
  return payload.items ?? []
}

export function analyzeDocument(params: {
  file: File
  documentType: string
  question: string
  includeBoxes?: boolean
}): Promise<AnalysisResponse> {
  const form = new FormData()
  form.append('file', params.file)
  form.append('document_type', params.documentType)
  form.append('question', params.question)
  form.append('include_boxes', String(params.includeBoxes ?? true))
  return getJson<AnalysisResponse>(`${BASE}/analyze`, { method: 'POST', body: form, headers: sessionHeaders() }).then((result) => {
    award('first-check')
    if (result.validation?.status === 'document_ok') award('complete-doc')
    return result
  })
}

/** Доп. вопрос к уже разобранному документу (OCR второй раз не гоняем). */
export function askQuestion(analysisId: string, question: string): Promise<Answer> {
  const form = new FormData()
  form.append('analysis_id', analysisId)
  form.append('question', question)
  return getJson(`${BASE}/ask`, { method: 'POST', body: form })
}
