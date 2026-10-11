import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ruleNumber } from '../lib/format'

interface RulesApi {
  /** Текст пункта по номеру вида «5.2». */
  textOf: (point: string) => string | undefined
  open: (point: string, anchor: HTMLElement) => void
}

const RulesContext = createContext<RulesApi>({ textOf: () => undefined, open: () => undefined })

interface ProviderProps {
  texts: Record<string, string>
  onGoTo: (point: string) => void
  children: ReactNode
}

/**
 * Всплывашка с текстом пункта Положения. Её открывает любой чип «п. N.M» в интерфейсе;
 * оттуда можно прыгнуть к пункту в разделе «Положение».
 */
export function RulesProvider({ texts, onGoTo, children }: ProviderProps) {
  const [state, setState] = useState<{ point: string; left: number; top: number } | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const anchorRef = useRef<HTMLElement | null>(null)

  const open = useCallback((point: string, anchor: HTMLElement) => {
    anchorRef.current = anchor
    const rect = anchor.getBoundingClientRect()
    setState({ point, left: rect.left + window.scrollX, top: rect.bottom + window.scrollY + 8 })
  }, [])

  // после отрисовки двигаем позицию, чтобы подсказка не уползала за экран
  useEffect(() => {
    const pop = popRef.current
    const anchor = anchorRef.current
    if (!state || !pop || !anchor) return
    const rect = anchor.getBoundingClientRect()
    const width = pop.offsetWidth
    const height = pop.offsetHeight
    const left = Math.max(16, Math.min(document.documentElement.clientWidth - width - 16, rect.left)) + window.scrollX
    const below = rect.bottom + height + 12 <= window.innerHeight
    const top = (below ? rect.bottom + 8 : rect.top - height - 8) + window.scrollY
    if (Math.abs(left - state.left) > 0.5 || Math.abs(top - state.top) > 0.5) setState({ ...state, left, top })
  }, [state])

  useEffect(() => {
    if (!state) return
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node
      if (popRef.current?.contains(target) || anchorRef.current?.contains(target)) return
      setState(null)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setState(null)
        anchorRef.current?.focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [state])

  const api = useMemo<RulesApi>(() => ({ textOf: (point) => texts[point], open }), [texts, open])

  return (
    <RulesContext.Provider value={api}>
      {children}
      {state ? (
        <div
          className="pop"
          ref={popRef}
          role="dialog"
          aria-label={`Положение, пункт ${state.point}`}
          style={{ left: state.left, top: state.top }}
        >
          <b>Положение, п. {state.point}</b>
          {texts[state.point] ?? 'Текст пункта сейчас недоступен: сервис не вернул Положение.'}
          <br />
          <button
            type="button"
            className="pop__go"
            onClick={() => {
              setState(null)
              onGoTo(state.point)
            }}
          >
            Открыть в Положении →
          </button>
        </div>
      ) : null}
    </RulesContext.Provider>
  )
}

/** Чип пункта Положения: «п. 5.2», тыкнули — показали текст пункта. */
export function RuleChip({ rule }: { rule: string }) {
  const { open } = useContext(RulesContext)
  const point = ruleNumber(rule)
  if (!point) return null
  return (
    <button
      type="button"
      className="rule"
      aria-haspopup="dialog"
      title={`Пункт ${point} Положения`}
      onClick={(event) => open(point, event.currentTarget)}
    >
      п. {point}
    </button>
  )
}
