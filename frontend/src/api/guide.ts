/**
 * API путеводителя: вход, профиль, вузы, история, AI-помощник.
 * Адреса тут без версии (/api/...), живут рядом с /api/v1, где проверка документов.
 */
import { ApiError } from './client'

const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'
/** Корень API: /api (а в мобилке — https://сервер/api). */
export const API_ROOT = BASE.replace(/\/v1\/?$/, '')
/** Адрес сервера, куда браузер реально переходит (вход через Яндекс). */
export const SERVER_ORIGIN = API_ROOT.replace(/\/api\/?$/, '')

const TOKEN_KEY = 'umir-token'
const UNIVERSITY_KEY = 'umir-university'

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function storeToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // приватный режим: вход доживёт только до перезагрузки, что поделать
  }
}

export function readUniversity(): string | null {
  try {
    return localStorage.getItem(UNIVERSITY_KEY)
  } catch {
    return null
  }
}

export function storeUniversity(id: string) {
  try {
    localStorage.setItem(UNIVERSITY_KEY, id)
  } catch {
    // ну и ладно
  }
}

/** Заголовки для запросов проверки: по ним история обращений цепляется к юзеру. */
export function sessionHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  const token = readToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const university = readUniversity()
  if (university) headers['X-University'] = university
  return headers
}

export const SESSION_EXPIRED_EVENT = 'umir:session-expired'

async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { ...sessionHeaders(), ...(init.headers as Record<string, string>) }
  let body = init.body
  if (init.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(init.json)
  }
  let response: Response
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...init, headers, body })
  } catch {
    throw new ApiError('Сервер приложения недоступен. Проверьте, что он запущен.', 0)
  }
  if (!response.ok) {
    // Сессия протухла или аккаунт снесли: говорим приложению, оно само выкинет на экран входа.
    if (response.status === 401 && /invalid_token/.test(response.headers.get('WWW-Authenticate') ?? '')) {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    }
    let message = `Ошибка ${response.status}`
    try {
      const payload = await response.json()
      if (typeof payload?.detail === 'string') message = payload.detail
      else if (Array.isArray(payload?.detail)) message = 'Проверьте заполнение полей.'
    } catch {
      // ну хоть код ответа оставим
    }
    throw new ApiError(message, response.status)
  }
  return response.json()
}

// ---------------------------------------------------------------- типы
export interface User {
  id: number
  login: string | null
  email: string | null
  name: string | null
  yandex_linked: boolean
  /** Общий демо-аккаунт для жюри: 2FA, пароль, Яндекс и удаление тут выключены, чтобы никто его не сломал. */
  demo?: boolean
  two_factor_enabled: boolean
  /** Сколько ещё неиспользованных кодов восстановления 2FA в запасе. */
  recovery_codes_left?: number
  university_id: string | null
  created_at: string
  has_password: boolean
}

export interface Session {
  access_token: string
  user: User
}

export type LoginResult = Session | { two_factor_required: true; challenge: string }

export interface AppConfig {
  team_name: string | null
  year: number
  repo_url: string | null
  version: string | null
  default_university: string | null
  assistant: { title?: string; subtitle?: string; greeting?: string; placeholder?: string; suggestions: string[] }
  auth: { yandex: boolean; demo?: boolean }
}

export type SectionState = 'available' | 'soon' | 'off'

export interface Building {
  id: string
  name: string
  address: string | null
  coords: [number, number] | null
  sources: string[]
}

export interface Faculty {
  id: string
  name: string
  kind?: string | null
  building_id?: string | null
  floor?: string | number | null
  room?: string | null
  dean?: string | null
  hours?: string | null
  phone?: string | null
  email?: string | null
  page?: string | null
  sources?: string[]
  note?: string | null
}

export interface UniversitySummary {
  id: string
  name: string
  short_name: string
  city: string | null
  site: string | null
  logo: string | null
  coords: [number, number]
  sections: Record<'dean' | 'faculties' | 'map' | 'history', SectionState>
  buildings: Building[]
  faculty_count: number
}

export interface University extends UniversitySummary {
  hall?: {
    title?: string
    welcome?: string
    building_image?: string | null
    building_caption?: string | null
    hall_image?: string | null
    hall_caption?: string | null
  }
  /** где на картинке корпуса двери — нужно для анимации входа */
  hall_doors?: [number, number]
  regulation_available: boolean
  regulation_note?: string | null
  faculties: Faculty[]
  data_note?: string | null
  sources?: string[]
}

export interface HistoryItem {
  id: number
  created_at: string
  university_id: string | null
  filename: string | null
  document_type: string | null
  document_type_title: string | null
  complete: boolean | null
  missing: string[]
  /** названия полей, которые забыли заполнить, — из конфига типов документов */
  missing_labels?: string[]
  question: string | null
}

export interface AssistantSource {
  label: string
  url?: string
  route?: string
}

export interface AssistantAnswer {
  answer: string
  kind: 'faq' | 'faculty' | 'rule' | 'none'
  title?: string
  confidence: number
  sources: AssistantSource[]
  suggestions: string[]
}

// ---------------------------------------------------------------- вызовы
export const guideApi = {
  config: () => request<AppConfig>('/app-config'),
  universities: () => request<{ items: UniversitySummary[] }>('/universities').then((p) => p.items),
  university: (id: string) => request<University>(`/universities/${encodeURIComponent(id)}`),

  register: (body: { login?: string; email?: string; password: string; name?: string; university_id?: string }) =>
    request<Session>('/auth/register', { method: 'POST', json: body }),
  login: (login: string, password: string) =>
    request<LoginResult>('/auth/login', { method: 'POST', json: { login, password } }),
  demo: () => request<Session>('/auth/demo', { method: 'POST' }),
  secondFactor: (challenge: string, code: string) =>
    request<Session>('/auth/2fa/login', { method: 'POST', json: { challenge, code } }),
  me: () => request<{ user: User }>('/auth/me').then((p) => p.user),
  updateMe: (body: { name?: string; university_id?: string }) =>
    request<{ user: User }>('/auth/me', { method: 'PATCH', json: body }).then((p) => p.user),
  totpSetup: () => request<{ secret: string; otpauth_url: string; qr_svg: string }>('/auth/2fa/setup', { method: 'POST' }),
  totpEnable: (code: string) =>
    request<{ user: User; recovery_codes: string[] }>('/auth/2fa/enable', { method: 'POST', json: { code } }),
  totpRecovery: (code: string) =>
    request<{ user: User; recovery_codes: string[] }>('/auth/2fa/recovery', { method: 'POST', json: { code } }),
  totpDisable: (code: string) =>
    request<{ user: User }>('/auth/2fa/disable', { method: 'POST', json: { code } }).then((p) => p.user),
  forgot: (login: string) => request<{ message: string }>('/auth/password/forgot', { method: 'POST', json: { login } }),
  reset: (token: string, password: string) =>
    request<Session>('/auth/password/reset', { method: 'POST', json: { token, password } }),
  changePassword: (password: string, current_password?: string) =>
    request<{ user: User }>('/auth/password/change', { method: 'POST', json: { password, current_password } }).then((p) => p.user),

  deleteAccount: (password?: string, code?: string) =>
    request<{ deleted: boolean }>('/auth/me/delete', { method: 'POST', json: { password, code } }),
  exportData: () => request<Record<string, unknown>>('/me/export'),
  yandexLink: () => request<{ url: string }>('/auth/yandex/link', { method: 'POST' }).then((p) => `${SERVER_ORIGIN}${p.url}`),
  yandexUnlink: () => request<{ user: User }>('/auth/yandex/unlink', { method: 'POST' }).then((p) => p.user),

  history: () => request<{ items: HistoryItem[] }>('/me/history').then((p) => p.items),
  clearHistory: () => request<{ deleted: number }>('/me/history', { method: 'DELETE' }),
  ask: (question: string, university_id?: string | null) =>
    request<AssistantAnswer>('/assistant/ask', { method: 'POST', json: { question, university_id } }),
}

export const yandexLoginUrl = `${SERVER_ORIGIN}/api/auth/yandex/login`
