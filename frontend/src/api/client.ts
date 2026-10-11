import type { AnalysisResponse, DocumentTypesResponse, HealthResponse } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

async function readError(response: Response): Promise<string> {
  try {
    const payload = await response.json()
    if (typeof payload?.detail === 'string') return payload.detail
    if (Array.isArray(payload?.detail)) {
      return payload.detail.map((item: { msg?: string }) => item.msg ?? 'ошибка').join('; ')
    }
    return `Ошибка ${response.status}`
  } catch {
    return `Ошибка ${response.status}: ${response.statusText}`
  }
}

export async function fetchDocumentTypes(): Promise<DocumentTypesResponse> {
  const response = await fetch(`${BASE}/document-types`)
  if (!response.ok) throw new Error(await readError(response))
  return response.json()
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${BASE}/health`)
  if (!response.ok) throw new Error(await readError(response))
  return response.json()
}

export async function analyzeDocument(params: {
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
  const response = await fetch(`${BASE}/analyze`, { method: 'POST', body: form })
  if (!response.ok) throw new Error(await readError(response))
  return response.json()
}
