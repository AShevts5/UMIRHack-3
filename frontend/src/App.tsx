import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { analyzeDocument, fetchDocumentTypes, fetchHealth } from './api/client'
import type { AnalysisResponse, DocumentTypesResponse, HealthResponse } from './api/types'
import AnswerCard from './components/AnswerCard'
import FieldCard from './components/FieldCard'
import type { Highlight } from './components/FieldCard'
import OcrText from './components/OcrText'
import UploadPanel from './components/UploadPanel'
import ValidationCard from './components/ValidationCard'
import { DocumentPreview } from './components/DocumentPreview'

const DEFAULT_QUESTION = 'Содержит ли документ все обязательные сведения по Положению?'

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [catalog, setCatalog] = useState<DocumentTypesResponse | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)

  const [file, setFile] = useState<File | null>(null)
  const [documentType, setDocumentType] = useState('auto')
  const [question, setQuestion] = useState(DEFAULT_QUESTION)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AnalysisResponse | null>(null)
  const [tab, setTab] = useState<'fields' | 'ocr'>('fields')
  const [highlight, setHighlight] = useState<Highlight | null>(null)
  const previewRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([fetchHealth(), fetchDocumentTypes()])
      .then(([healthPayload, catalogPayload]) => {
        if (cancelled) return
        setHealth(healthPayload)
        setCatalog(catalogPayload)
        setQuestion((current) => (current === DEFAULT_QUESTION ? catalogPayload.default_question : current))
      })
      .catch((reason: Error) => {
        if (!cancelled) setBootError(reason.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const previewUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file])

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  const runAnalysis = useCallback(
    async (options?: { question?: string; checkOnly?: boolean }) => {
      if (!file) {
        setError('Сначала выберите файл документа (PNG, JPG или одностраничный PDF).')
        return
      }
      setBusy(true)
      setError(null)
      setHighlight(null)
      try {
        const asked = options?.checkOnly ? DEFAULT_QUESTION : options?.question ?? question
        if (options?.checkOnly) setQuestion(DEFAULT_QUESTION)
        const payload = await analyzeDocument({ file, documentType, question: asked })
        setResult(payload)
        setTab('fields')
      } catch (reason) {
        setResult(null)
        setError((reason as Error).message)
      } finally {
        setBusy(false)
      }
    },
    [documentType, file, question],
  )

  const onSelectField = useCallback((box: Highlight['box'], label: string) => {
    setHighlight({ box, label, kind: 'field' })
    previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [])

  const onSelectMark = useCallback((box: Highlight['box'], label: string) => {
    setHighlight({ box, label, kind: 'mark' })
    previewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [])

  const healthOk = health?.status === 'ok'

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand__mark" aria-hidden>
            ОД
          </div>
          <div>
            <h1 className="brand__title">Онлайн-деканат</h1>
            <p className="brand__subtitle">
              Предварительная проверка студенческих документов по Положению университета «Меридиан»
            </p>
          </div>
        </div>
        <div className="health" title={health?.ocr.binary ?? ''}>
          <span className={healthOk ? 'dot' : 'dot dot--warn'} />
          {health
            ? healthOk
              ? `Локальный OCR готов · ${health.ocr.engine} ${health.ocr.version ?? ''} · ${health.ocr.languages.join(', ')}`
              : `OCR недоступен: ${health.ocr.error ?? 'неизвестная причина'}`
            : 'Проверка окружения…'}
        </div>
      </header>

      {bootError ? (
        <div className="error" style={{ marginBottom: 16 }}>
          Не удалось получить данные сервиса: {bootError}. Убедитесь, что backend запущен командой{' '}
          <code>python -m backend.scripts.tasks api</code>.
        </div>
      ) : null}

      <div className="grid">
        <div className="stack">
          <UploadPanel
            file={file}
            catalog={catalog}
            documentType={documentType}
            question={question}
            busy={busy}
            previewUrl={previewUrl}
            onFileChange={(next) => {
              setFile(next)
              setResult(null)
              setError(null)
            }}
            onDocumentTypeChange={setDocumentType}
            onQuestionChange={setQuestion}
            onAnalyze={() => runAnalysis()}
            onCheckOnly={() => runAnalysis({ checkOnly: true })}
          />
          {error ? <div className="error">{error}</div> : null}
        </div>

        <div className="stack">
          {result ? (
            <>
              <section className="card">
                <div className="section-title">
                  <span>Предварительное заключение</span>
                  <span className="badge">
                    {result.document_type_title || result.document_type}
                    {result.document_type_source === 'auto'
                      ? ` · тип определён автоматически (${Math.round(result.document_type_confidence * 100)}%)`
                      : ''}
                  </span>
                </div>
                <ValidationCard validation={result.validation} message={result.validation_message} />
                <AnswerCard answer={result.answer} question={question} />
                {result.warnings.length > 0 ? (
                  <ul className="disclaimer">
                    {result.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                ) : null}
              </section>

              <section className="card">
                <div className="section-title">
                  <span>Досье документа</span>
                  <span className="badge">
                    OCR {result.ocr_engine} · {result.ocr_languages} · средняя уверенность{' '}
                    {result.ocr_mean_confidence.toFixed(1)}%
                  </span>
                </div>
                <div className="tabs" role="tablist">
                  <button
                    className={tab === 'fields' ? 'tab tab--active' : 'tab'}
                    onClick={() => setTab('fields')}
                    role="tab"
                    aria-selected={tab === 'fields'}
                  >
                    Карточка полей
                  </button>
                  <button
                    className={tab === 'ocr' ? 'tab tab--active' : 'tab'}
                    onClick={() => setTab('ocr')}
                    role="tab"
                    aria-selected={tab === 'ocr'}
                  >
                    Распознанный текст
                  </button>
                </div>
                {tab === 'fields' ? (
                  <FieldCard
                    fieldViews={result.field_views}
                    markViews={result.mark_views}
                    activeKey={highlight?.kind === 'field' ? highlight.label : null}
                    onSelect={onSelectField}
                    onSelectMark={onSelectMark}
                  />
                ) : (
                  <OcrText text={result.ocr_text} boxes={result.boxes} highlight={highlight?.label ?? null} />
                )}
              </section>

              <section className="card">
                <div ref={previewRef} className="section-title">
                  <span>Исходный документ и найденные места</span>
                  {highlight ? <span className="badge">{highlight.label}</span> : <span className="badge">нажмите на поле</span>}
                </div>
                <DocumentPreview url={previewUrl} highlight={highlight} />
                <p className="disclaimer">
                  Рамкой отмечены зоны, к которым относится выбранный элемент: координаты слов приходят из локального OCR,
                  зоны подписи и печати — из анализа графики.
                </p>
              </section>
            </>
          ) : (
            <section className="card">
              <h2 className="card__title">Как это работает</h2>
              <ol className="card__hint" style={{ paddingLeft: 18 }}>
                <li>Загрузите PNG, JPG или одностраничный PDF студенческого документа.</li>
                <li>Выберите тип документа (или оставьте «Определить автоматически» — это бонус).</li>
                <li>Задайте вопрос на русском или нажмите «Проверить документ».</li>
                <li>
                  Сервис локально распознаёт текст, собирает карточку полей, проверяет обязательные сведения по пунктам
                  Положения и показывает источники каждого утверждения.
                </li>
              </ol>
              <p className="disclaimer">
                Обработка выполняется на этом компьютере: изображения не отправляются во внешние OCR- или LLM-сервисы.
                Сервис даёт только предварительную проверку — окончательное решение принимает сотрудник деканата
                (пункт 1.3 Положения).
              </p>
              {catalog ? (
                <dl className="kv" style={{ marginTop: 14 }}>
                  {catalog.document_types.map((type) => (
                    <div key={type.code} style={{ display: 'contents' }}>
                      <dt>{type.title}</dt>
                      <dd>
                        {type.fields.length} обязательных текстовых полей, {type.marks.length} графических отметок ·
                        пункты {type.required_rules.map((rule) => rule.replace('rule:', '')).join(', ')}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </section>
          )}
        </div>
      </div>

      <p className="footer">
        Учебный кейс «Онлайн-деканат» (UMIR 3). Все документы и Положение вымышлены, совпадения случайны. Сервис не
        сообщает «документ принят» и не оформляет перевод, отпуск или зачёт практики.
      </p>
    </div>
  )
}
