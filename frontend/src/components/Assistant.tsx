import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError } from '../api/client'
import type { AssistantAnswer, AssistantSource } from '../api/guide'
import { guideApi } from '../api/guide'
import { award } from '../lib/badges'
import { navigate } from '../lib/router'
import { useGuide } from '../lib/session'
import { ChatIcon, CloseIcon, MicIcon, RobotIcon, SpeakerIcon } from './GuideIcons'

interface Message {
  id: number
  from: 'bot' | 'me'
  text: string
  sources?: AssistantSource[]
  pending?: boolean
}

const GREETING = 'Приветствую Повелителя своего! Чем могу быть полезен?'
const OPEN_EVENT = 'umir:assistant'
const STORE_KEY = 'umir-assistant'

interface Saved {
  open: boolean
  messages: Message[]
  suggestions: string[]
}

// Диалог храним в sessionStorage: при переходе между экранами (виджет монтируется заново)
// он не пропадает, а закрыл вкладку — всё начинается снова с приветствия.
function readSaved(): Saved | null {
  try {
    const raw = sessionStorage.getItem(STORE_KEY)
    if (!raw) return null
    const saved = JSON.parse(raw) as Saved
    return Array.isArray(saved.messages) && saved.messages.length ? saved : null
  } catch {
    return null
  }
}

function writeSaved(saved: Saved) {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify({ ...saved, messages: saved.messages.filter((m) => !m.pending).slice(-40) }))
  } catch {
    // приватный режим: ну не сохраним, и ладно
  }
}

/** Открываем помощника с любого экрана (например, с поста охраны в холле), можно сразу с вопросом. */
export function openAssistant(question?: string) {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { question } }))
}

// ---- Web Speech API: типы нестандартные, описываем по минимуму, что реально юзаем
interface RecognitionLike {
  lang: string
  interimResults: boolean
  maxAlternatives: number
  processLocally?: boolean
  start(): void
  stop(): void
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
}
type RecognitionCtor = {
  new (): RecognitionLike
  available?: (options: { langs: string[]; processLocally: boolean }) => Promise<string>
}

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

function speak(text: string) {
  if (!('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'ru-RU'
  const voice = window.speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith('ru'))
  if (voice) utterance.voice = voice
  window.speechSynthesis.speak(utterance)
}

/**
 * AI-помощник: виджет в правом нижнем углу, есть на всех экранах.
 * Ответы ищет наш локальный бэкенд (/api/assistant/ask) по справке приложения, данным
 * вузов и Положению — никаких внешних LLM. Голос: распознавание и озвучка силами браузера.
 */
export default function Assistant() {
  const { config, universityId } = useGuide()
  const greeting = config?.assistant.greeting || GREETING
  const [saved] = useState(readSaved)
  const [open, setOpen] = useState(saved?.open ?? false)
  const [messages, setMessages] = useState<Message[]>(saved?.messages ?? [{ id: 0, from: 'bot', text: greeting }])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const [voiceNote, setVoiceNote] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<string[]>(saved?.suggestions ?? [])
  const idRef = useRef(Math.max(0, ...(saved?.messages ?? []).map((m) => m.id)) + 1)
  const listRef = useRef<HTMLDivElement | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const fabRef = useRef<HTMLButtonElement | null>(null)
  const wasOpen = useRef(false)
  const pendingQuestion = useRef<string | null>(null)
  const recognitionRef = useRef<RecognitionLike | null>(null)
  const speakNext = useRef(false)
  const canListen = recognitionCtor() !== null
  const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window

  useEffect(() => {
    setMessages((items) => (items.length === 1 && items[0].from === 'bot' ? [{ ...items[0], text: greeting }] : items))
  }, [greeting])

  useEffect(() => {
    if (config && !saved?.suggestions.length) setSuggestions(config.assistant.suggestions.slice(0, 4))
  }, [config, saved])

  useEffect(() => {
    if (!busy) writeSaved({ open, messages, suggestions })
  }, [open, messages, suggestions, busy])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, open])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    inputRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // После закрытия фокус возвращаем на кнопку помощника, а то он улетает в начало страницы.
  useEffect(() => {
    if (wasOpen.current && !open) fabRef.current?.focus()
    wasOpen.current = open
  }, [open])

  const ask = useCallback(
    async (question: string) => {
      const text = question.trim()
      if (!text || busy) return
      const pendingId = idRef.current + 1
      idRef.current += 2
      setMessages((items) => [...items, { id: pendingId - 1, from: 'me', text }, { id: pendingId, from: 'bot', text: '…', pending: true }])
      setInput('')
      setBusy(true)
      let reply: AssistantAnswer | null = null
      let failure: string | null = null
      try {
        reply = await guideApi.ask(text, universityId)
        award('ask')
      } catch (reason) {
        failure = reason instanceof ApiError ? reason.message : 'Помощник сейчас недоступен.'
      }
      setMessages((items) =>
        items.map((item) =>
          item.id === pendingId
            ? { id: pendingId, from: 'bot', text: reply?.answer ?? failure ?? '', sources: reply?.sources ?? [] }
            : item,
        ),
      )
      if (reply?.suggestions?.length) setSuggestions(reply.suggestions.slice(0, 4))
      if (reply && speakNext.current) speak(reply.answer)
      speakNext.current = false
      setBusy(false)
    },
    [busy, universityId],
  )

  useEffect(() => {
    const onOpen = (event: Event) => {
      const question = (event as CustomEvent<{ question?: string }>).detail?.question
      setOpen(true)
      if (question) pendingQuestion.current = question
    }
    window.addEventListener(OPEN_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_EVENT, onOpen)
  }, [])

  useEffect(() => {
    if (!open || !pendingQuestion.current || busy) return
    const question = pendingQuestion.current
    pendingQuestion.current = null
    void ask(question)
  }, [open, busy, ask])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void ask(input)
  }

  const reset = () => {
    window.speechSynthesis?.cancel()
    setMessages([{ id: idRef.current++, from: 'bot', text: greeting }])
    setSuggestions(config?.assistant.suggestions.slice(0, 4) ?? [])
  }

  async function listen() {
    const Ctor = recognitionCtor()
    if (!Ctor) return
    if (listening) {
      recognitionRef.current?.stop()
      return
    }
    const recognition = new Ctor()
    recognition.lang = 'ru-RU'
    recognition.interimResults = false
    recognition.maxAlternatives = 1
    // Распознаём прямо на устройстве, если браузер умеет (Chrome 139+): звук никуда не утекает.
    let local = false
    if (typeof Ctor.available === 'function') {
      try {
        local = (await Ctor.available({ langs: ['ru-RU'], processLocally: true })) === 'available'
      } catch {
        local = false
      }
    }
    if (local) recognition.processLocally = true
    setVoiceNote(local ? 'Распознаём на устройстве' : 'Распознавание средствами браузера')
    recognition.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript ?? ''
      if (transcript) {
        speakNext.current = true
        award('voice')
        void ask(transcript)
      }
    }
    recognition.onerror = (event) => {
      setVoiceNote(event.error === 'not-allowed' ? 'Нет доступа к микрофону' : 'Не расслышал, попробуйте ещё раз')
    }
    recognition.onend = () => setListening(false)
    recognitionRef.current = recognition
    setListening(true)
    recognition.start()
  }

  const follow = (source: AssistantSource) => {
    if (source.url) window.open(source.url, '_blank', 'noopener')
    else if (source.route) {
      navigate(source.route.replace(/^#/, ''))
      if (window.matchMedia('(max-width: 640px)').matches) setOpen(false)
    }
  }

  return (
    <div className="ai">
      <AnimatePresence>
        {open ? (
          <motion.div
            className="ai__panel force-dark"
            id="ai-panel"
            role="dialog"
            aria-label="AI-помощник"
            initial={{ opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ duration: 0.18 }}
          >
            <header className="ai__head">
              <RobotIcon />
              <div>
                <b>{config?.assistant.title || 'AI-помощник'}</b>
                <small>{config?.assistant.subtitle || 'Ответы об Онлайн-деканате и вузе'}</small>
              </div>
              <button type="button" className="ai__new" onClick={reset}>
                Новый диалог
              </button>
              <button type="button" className="ai__x" aria-label="Закрыть помощника" onClick={() => setOpen(false)}>
                <CloseIcon size={18} />
              </button>
            </header>

            <div className="ai__list" ref={listRef} aria-live="polite">
              {messages.map((message) => (
                <div key={message.id} className={`ai__msg ai__msg--${message.from}${message.pending ? ' is-pending' : ''}`}>
                  <p>{message.text}</p>
                  {message.from === 'bot' && !message.pending && (message.sources?.length || canSpeak) ? (
                    <div className="ai__src">
                      {message.sources?.map((source) => (
                        <button key={`${source.label}-${source.url ?? source.route}`} type="button" onClick={() => follow(source)}>
                          {source.label}
                        </button>
                      ))}
                      {canSpeak ? (
                        <button type="button" className="ai__say" aria-label="Озвучить ответ" title="Озвучить" onClick={() => speak(message.text)}>
                          <SpeakerIcon size={15} />
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>

            {suggestions.length ? (
              <div className="ai__chips">
                {suggestions.map((item) => (
                  <button key={item} type="button" onClick={() => ask(item)} disabled={busy}>
                    {item}
                  </button>
                ))}
              </div>
            ) : null}

            <form className="ai__form" onSubmit={submit}>
              <input
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={config?.assistant.placeholder || 'Задайте вопрос о платформе…'}
                aria-label="Вопрос помощнику"
                maxLength={500}
              />
              {canListen ? (
                <button
                  type="button"
                  className={listening ? 'ai__mic is-on' : 'ai__mic'}
                  aria-pressed={listening}
                  aria-label={listening ? 'Остановить запись' : 'Спросить голосом'}
                  title={voiceNote ?? 'Спросить голосом'}
                  onClick={listen}
                >
                  <MicIcon />
                </button>
              ) : null}
              <button type="submit" className="ai__send" aria-label="Отправить" disabled={!input.trim() || busy}>
                →
              </button>
            </form>
            {listening || voiceNote ? <p className="ai__voice">{listening ? 'Слушаю…' : voiceNote}</p> : null}
          </motion.div>
        ) : null}
      </AnimatePresence>

      <button
        type="button"
        className="ai__fab"
        ref={fabRef}
        aria-expanded={open}
        aria-controls="ai-panel"
        aria-label={open ? 'Закрыть помощника' : 'Открыть AI-помощника'}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <CloseIcon size={24} /> : <ChatIcon />}
      </button>
    </div>
  )
}
