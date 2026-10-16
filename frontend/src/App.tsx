import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, analyzeDocument, askQuestion, fetchDocumentTypes, fetchHealth, fetchRegulation } from './api/client'
import type { AnalysisResponse, DocumentTypesResponse, HealthResponse, RegulationItem } from './api/types'
import AnswerCard from './components/AnswerCard'
import type { QaEntry } from './components/AnswerCard'
import DocumentPreview from './components/DocumentPreview'
import type { StageHandle } from './components/DocumentPreview'
import FieldCard from './components/FieldCard'
import { AlertIcon, Logo } from './components/Icons'
import OcrText from './components/OcrText'
import Processing from './components/Processing'
import { RulesProvider } from './components/RulePopover'
import RulesView from './components/RulesView'
import ThemeSwitch from './components/ThemeSwitch'
import UploadPanel from './components/UploadPanel'
import type { StartError } from './components/UploadPanel'
import ValidationCard from './components/ValidationCard'
import { buildGroups, isImageFile } from './lib/format'
import { useTheme } from './lib/theme'
import type { ThemeMode } from './lib/theme'

const DEFAULT_QUESTION = 'Содержит ли документ все обязательные сведения?'
const ALLOWED = ['.png', '.jpg', '.jpeg', '.pdf']
const LOW_TYPE_CONFIDENCE = 0.6

type View = 'check' | 'rules'
type Phase = 'start' | 'processing' | 'result'
type Tab = 'req' | 'qa' | 'ocr' | 'json'

function readExpert(): boolean {
  try {
    return localStorage.getItem('umir-expert') === '1'
  } catch {
    return false
  }
}

/** Человеческий текст ошибки анализа: что случилось и что с этим делать. */
function describeError(reason: unknown, retry: () => void, pickOther: () => void): StartError {
  if (reason instanceof ApiError) {
    if (reason.status === 422 || reason.status === 400 || reason.status === 413) {
      return { title: reason.message, action: { label: 'Выбрать другой файл', run: pickOther } }
    }
    if (reason.status === 503) {
      return { title: 'Локальный OCR недоступен', text: reason.message }
    }
    return {
      title: 'Не удалось обработать документ',
      text: 'Попробуйте ещё раз или выберите другой файл.',
      action: { label: 'Повторить', run: retry },
    }
  }
  return {
    title: 'Сервис проверки не запущен',
    text: 'Запустите python -m backend.scripts.tasks api и обновите страницу.',
    action: { label: 'Повторить', run: retry },
  }
}

interface AppProps {
  /** тема приходит из общей оболочки (Root), чтобы переключатель был один на всё приложение */
  theme?: ThemeMode
  onTheme?: (mode: ThemeMode) => void
  /** сразу открыть раздел «Положение» на этом пункте (для ссылок помощника) */
  initialRule?: string | null
  universityTitle?: string
  leading?: ReactNode
  trailing?: ReactNode
}

/** Раздел «Обращение в деканат»: главный пайплайн кейса (проверка документа). */
export default function App({ theme: sharedTheme, onTheme, initialRule, universityTitle, leading, trailing }: AppProps = {}) {
  const [ownTheme, setOwnTheme] = useTheme()
  const theme = sharedTheme ?? ownTheme
  const setTheme = onTheme ?? setOwnTheme
  const [expert, setExpert] = useState(readExpert)
  const [view, setView] = useState<View>(initialRule ? 'rules' : 'check')
  const [flashRule, setFlashRule] = useState<string | null>(initialRule ?? null)

  useEffect(() => {
    if (initialRule) {
      setView('rules')
      setFlashRule(initialRule)
    }
  }, [initialRule])

  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [catalog, setCatalog] = useState<DocumentTypesResponse | null>(null)
  const [regulation, setRegulation] = useState<RegulationItem[] | null>(null)
  const [bootFailed, setBootFailed] = useState(false)

  const [phase, setPhase] = useState<Phase>('start')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<AnalysisResponse | null>(null)
  const [timings, setTimings] = useState<Record<string, number> | null>(null)
  const [startError, setStartError] = useState<StartError | null>(null)
  const [tab, setTab] = useState<Tab>('req')
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [qa, setQa] = useState<QaEntry[]>([])
  const [asking, setAsking] = useState(false)
  const [copied, setCopied] = useState(false)
  const stageRef = useRef<StageHandle | null>(null)
  const runId = useRef(0)
  const qaId = useRef(0)

  // ---- старт: живой ли OCR, типы документов и Положение
  const boot = useCallback(() => {
    setBootFailed(false)
    Promise.all([fetchHealth(), fetchDocumentTypes()])
      .then(([healthPayload, catalogPayload]) => {
        setHealth(healthPayload)
        setCatalog(catalogPayload)
      })
      .catch(() => setBootFailed(true))
    fetchRegulation()
      .then(setRegulation)
      .catch(() => setRegulation([]))
  }, [])

  useEffect(boot, [boot])

  useEffect(() => {
    try {
      localStorage.setItem('umir-expert', expert ? '1' : '0')
    } catch {
      // не страшно: режим просто не запомнится, переживём
    }
    if (!expert && tab === 'json') setTab('req')
  }, [expert, tab])

  const ruleTexts = useMemo(() => {
    const map: Record<string, string> = {}
    for (const section of regulation ?? []) for (const clause of section.children) map[clause.point] = clause.text
    return map
  }, [regulation])

  const requiredRules = useMemo(
    () =>
      new Set(
        (catalog?.document_types ?? []).flatMap((type) => type.required_rules.map((rule) => rule.replace('rule:', ''))),
      ),
    [catalog],
  )

  const previewUrl = useMemo(() => (file && isImageFile(file) ? URL.createObjectURL(file) : null), [file])
  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const goToRule = useCallback((point: string) => {
    setView('rules')
    setFlashRule(point)
  }, [])

  const backToStart = useCallback(() => {
    runId.current += 1
    setPhase('start')
    setResult(null)
    setQa([])
    setActiveKey(null)
    setStartError(null)
    window.scrollTo({ top: 0 })
  }, [])

  // ---- анализ, самое главное
  const analyze = useCallback(
    async (picked: File, documentType = 'auto') => {
      const maxBytes = catalog?.limits.max_upload_bytes ?? 12 * 1024 * 1024
      const suffix = picked.name.slice(picked.name.lastIndexOf('.')).toLowerCase()
      const allowed = catalog?.limits.allowed_suffixes?.length ? catalog.limits.allowed_suffixes : ALLOWED
      setStartError(null)
      if (!allowed.includes(suffix)) {
        setStartError({ title: 'Этот формат не подходит. Загрузите PNG, JPG или одностраничный PDF.' })
        return
      }
      if (picked.size > maxBytes) {
        const mb = Math.round(maxBytes / (1024 * 1024))
        setStartError({ title: `Файл больше ${mb} МБ. Сожмите изображение или сохраните страницу в PNG.` })
        return
      }

      const id = ++runId.current
      setFile(picked)
      setTimings(null)
      setPhase('processing')
      setActiveKey(null)
      setQa([])
      window.scrollTo({ top: 0 })
      try {
        const payload = await analyzeDocument({ file: picked, documentType, question: DEFAULT_QUESTION })
        if (id !== runId.current) return
        setTimings(payload.timings_ms ?? {})
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
        await new Promise((resolve) => setTimeout(resolve, reduced ? 0 : 450))
        if (id !== runId.current) return
        setResult(payload)
        setQa([{ id: ++qaId.current, question: DEFAULT_QUESTION, answer: payload.answer }])
        setTab('req')
        setPhase('result')
      } catch (reason) {
        if (id !== runId.current) return
        setPhase('start')
        setResult(null)
        setStartError(
          describeError(
            reason,
            () => analyze(picked, documentType),
            () => setStartError(null),
          ),
        )
      }
    },
    [catalog],
  )

  // ---- вопросы: OCR второй раз не гоняем, если бэкенд умеет /ask
  const ask = useCallback(
    async (question: string) => {
      if (!result || !file) return
      const entryId = ++qaId.current
      setQa((items) => [...items, { id: entryId, question, answer: null }])
      setAsking(true)
      try {
        let answer
        try {
          if (!result.analysis_id) throw new ApiError('no id', 404)
          answer = await askQuestion(result.analysis_id, question)
        } catch (reason) {
          if (!(reason instanceof ApiError) || (reason.status !== 404 && reason.status !== 405)) throw reason
          answer = (await analyzeDocument({ file, documentType: result.document_type, question })).answer
        }
        setQa((items) => items.map((item) => (item.id === entryId ? { ...item, answer } : item)))
      } catch (reason) {
        const message = reason instanceof ApiError ? reason.message : 'Сервис проверки не ответил. Попробуйте ещё раз.'
        setQa((items) => items.map((item) => (item.id === entryId ? { ...item, error: message } : item)))
      } finally {
        setAsking(false)
      }
    },
    [file, result],
  )

  const groups = useMemo(() => (result ? buildGroups(result, catalog) : []), [result, catalog])
  const items = useMemo(() => groups.flatMap((group) => group.items), [groups])

  const select = useCallback(
    (key: string, fromStage: boolean) => {
      const next = activeKey === key && fromStage ? null : key
      setActiveKey(next)
      if (!next) return
      if (fromStage) {
        setTab('req')
        requestAnimationFrame(() =>
          document.querySelector(`[data-row="${CSS.escape(key)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }),
        )
      } else {
        requestAnimationFrame(() => stageRef.current?.reveal(key, true))
      }
    },
    [activeKey],
  )

  const suggestions = useMemo(() => {
    if (!result) return []
    const dateField = result.field_views.find((field) => /дата выдачи|дата заявления/i.test(field.label)) ?? result.field_views[0]
    return [
      DEFAULT_QUESTION,
      dateField ? `Укажи значение поля «${dateField.label}».` : null,
      'Кто принимает окончательное решение о принятии документа?',
      'Какая стипендия мне положена?',
    ].filter((item): item is string => Boolean(item))
  }, [result])

  const healthOk = health?.status === 'ok'
  const blocked = bootFailed
    ? 'Сервис проверки не запущен'
    : health && !healthOk
      ? `OCR недоступен: ${health.ocr.error ?? 'неизвестная причина'}`
      : null

  const found = items.filter((item) => item.found).length
  const activeLabel = items.find((item) => item.key === activeKey && item.kind === 'field')?.label ?? null
  const imageUrl = result?.page_image_url ?? previewUrl
  const typeTitle = result ? result.document_type_title || result.document_type : ''

  const resultsJson = useMemo(() => {
    if (!result || !file) return ''
    return JSON.stringify(
      {
        document_id: result.document_id ?? file.name.replace(/\.[^.]+$/, ''),
        document_type: result.document_type,
        fields: result.fields,
        validation: result.validation,
        answers: qa.filter((entry) => entry.answer).map((entry) => entry.answer),
      },
      null,
      2,
    )
  }, [file, qa, result])

  return (
    <RulesProvider texts={ruleTexts} onGoTo={goToRule}>
      <header className="bar">
        <div className="bar__in">
          <div className="brand">
            {leading}
            <Logo />
            <div>
              <b>Академиум</b>
              <small>{universityTitle ?? 'Онлайн-деканат'}</small>
            </div>
          </div>
          <nav className="seg" aria-label="Разделы">
            <button type="button" aria-pressed={view === 'check'} onClick={() => setView('check')}>
              Проверка
            </button>
            <button
              type="button"
              aria-pressed={view === 'rules'}
              onClick={() => {
                setFlashRule(null)
                setView('rules')
                window.scrollTo({ top: 0 })
              }}
            >
              Положение
            </button>
          </nav>
          <span
            className={healthOk || !health ? 'pill pill-ocr' : 'pill pill-ocr pill--warn'}
            title={health?.ocr.binary ?? undefined}
          >
            <span className={healthOk ? 'dot' : 'dot dot--warn'} />
            {bootFailed
              ? 'Сервис проверки не запущен'
              : health
                ? healthOk
                  ? `OCR на этом компьютере · ${health.ocr.languages.join(', ')}`
                  : `OCR недоступен: ${health.ocr.error ?? 'неизвестная причина'}`
                : 'Проверяем окружение…'}
          </span>
          <div className="bar__tools">
            <ThemeSwitch mode={theme} onChange={setTheme} />
            <button
              type="button"
              className="switch"
              role="switch"
              aria-checked={expert}
              aria-label="Режим эксперта"
              onClick={() => setExpert((value) => !value)}
            >
              <i />
              <span>Режим эксперта</span>
            </button>
            {trailing}
          </div>
        </div>
      </header>

      <main className="wrap">
        {view === 'rules' ? (
          <RulesView sections={regulation} required={requiredRules} flash={flashRule} />
        ) : (
          <>
            {bootFailed && phase === 'start' ? (
              <div className="alert" role="alert" style={{ marginTop: 24 }}>
                <AlertIcon />
                <div>
                  <p>
                    <b>Сервис проверки не запущен.</b>
                  </p>
                  <p>
                    Запустите <code>python -m backend.scripts.tasks api</code> и обновите страницу.
                  </p>
                  <button type="button" className="btn" onClick={boot}>
                    Повторить
                  </button>
                </div>
              </div>
            ) : null}

            {phase === 'start' ? (
              <UploadPanel catalog={catalog} blocked={blocked} error={startError} onFile={(picked) => analyze(picked)} />
            ) : null}

            {phase === 'processing' && file ? (
              <Processing fileName={file.name} thumbUrl={previewUrl} timings={timings} />
            ) : null}

            {phase === 'result' && result && file ? (
              <div className="ws">
                <DocumentPreview
                  ref={stageRef}
                  fileName={file.name}
                  imageUrl={imageUrl}
                  width={result.page_image_url ? result.page_width ?? 0 : 0}
                  height={result.page_image_url ? result.page_height ?? 0 : 0}
                  items={items}
                  activeKey={activeKey}
                  onSelect={(key) => select(key, true)}
                  originalUrl={result.page_enhanced && result.page_image_url ? previewUrl : null}
                />

                <div className="panel">
                  <div className="typeline">
                    <span className="tag tag--none" style={{ fontWeight: 500 }}>
                      {typeTitle}
                    </span>
                    <span className="muted">
                      {result.document_type_source === 'auto'
                        ? `автоопределение · ${Math.round(result.document_type_confidence * 100)}%`
                        : 'тип выбран вручную'}
                    </span>
                    <label className="sr-only" htmlFor="retype">
                      Изменить тип документа
                    </label>
                    <select
                      id="retype"
                      className="sp"
                      value=""
                      onChange={(event) => event.target.value && analyze(file, event.target.value)}
                    >
                      <option value="">изменить</option>
                      {catalog?.document_types.map((type) => (
                        <option key={type.code} value={type.code}>
                          {type.title}
                        </option>
                      ))}
                    </select>
                    <button type="button" className="btn btn--quiet" onClick={backToStart}>
                      Проверить другой документ
                    </button>
                  </div>

                  {result.document_type_source === 'auto' && result.document_type_confidence < LOW_TYPE_CONFIDENCE ? (
                    <div className="alert alert--info" role="status">
                      <AlertIcon />
                      <p>
                        Не уверены в типе документа. Проверьте: <b>{typeTitle}</b>. Если тип другой, выберите его в
                        списке «Изменить тип» выше.
                      </p>
                    </div>
                  ) : null}

                  <ValidationCard result={result} items={items} onAsk={() => setTab('qa')} onFix={backToStart} />

                  {result.warnings.length ? (
                    <div className="note" role="note">
                      <AlertIcon />
                      <div>
                        {result.warnings.map((warning) => (
                          <p key={warning}>{warning}</p>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  <div>
                    <div className="tabs" role="tablist" aria-label="Разделы результата">
                      {(
                        [
                          ['req', 'Требования', `${found}/${items.length}`],
                          ['qa', 'Вопросы', qa.length ? String(qa.length) : ''],
                          ['ocr', 'Распознанный текст', ''],
                          ...(expert ? [['json', 'results.json', '']] : []),
                        ] as [Tab, string, string][]
                      ).map(([key, label, count]) => (
                        <button
                          key={key}
                          type="button"
                          role="tab"
                          id={`tab-${key}`}
                          aria-selected={tab === key}
                          aria-controls={`panel-${key}`}
                          onClick={() => setTab(key)}
                        >
                          {label}
                          {count ? <span className="count">{count}</span> : null}
                        </button>
                      ))}
                    </div>
                    <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
                      {tab === 'req' ? (
                        <FieldCard groups={groups} activeKey={activeKey} expert={expert} onSelect={(key) => select(key, false)} />
                      ) : null}
                      {tab === 'qa' ? (
                        <AnswerCard
                          entries={qa}
                          suggestions={suggestions}
                          items={items}
                          expert={expert}
                          busy={asking}
                          onAsk={ask}
                          onShowField={(key) => select(key, false)}
                        />
                      ) : null}
                      {tab === 'ocr' ? (
                        <OcrText
                          text={result.ocr_text}
                          highlight={activeLabel}
                          meanConfidence={result.ocr_mean_confidence}
                          wordCount={result.boxes.length}
                          expert={expert}
                          engine={result.ocr_engine}
                          languages={result.ocr_languages}
                        />
                      ) : null}
                      {tab === 'json' && expert ? (
                        <div>
                          <pre className="json">{resultsJson}</pre>
                          <button
                            type="button"
                            className="btn copy"
                            onClick={() => {
                              navigator.clipboard
                                ?.writeText(resultsJson)
                                .then(() => {
                                  setCopied(true)
                                  window.setTimeout(() => setCopied(false), 1800)
                                })
                                .catch(() => undefined)
                            }}
                          >
                            {copied ? 'Скопировано' : 'Скопировать'}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
          </>
        )}

        <p className="foot">
          Обработка идёт на этом компьютере: изображения не отправляются во внешние OCR- или LLM-сервисы.
        </p>
      </main>
    </RulesProvider>
  )
}
