import type { AnalysisResponse, BoundingBox, DocumentTypesResponse } from '../api/types'

export const NOT_FOUND = 'не найдено'

/** Склоняем по-русски после числа: 1 поле, 2 поля, 5 полей. */
export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}

/** «rule:5.2» → «5.2», просто отрезаем префикс. */
export function ruleNumber(source: string): string {
  return source.replace(/^rule:/, '')
}

/** Пайплайн отдаёт даты как ГГГГ-ММ-ДД, а студенту показываем как в самом документе. */
export function displayValue(value: string, expert: boolean): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match || expert) return value
  return `${match[3]}.${match[2]}.${match[1]}`
}

export interface RequirementItem {
  key: string
  n: number
  kind: 'field' | 'mark'
  label: string
  value: string | null
  found: boolean
  box: BoundingBox | null
  confidence: number
  method: string
}

export interface RequirementGroup {
  rule: string
  title: string
  items: RequirementItem[]
}

/**
 * Требования по пунктам Положения: все текстовые поля пункта сваливаем в одну группу,
 * а каждая графическая отметка — своя отдельная группа. Нумерация сквозная: тот же номер
 * висит на булавке зоны на листе, не сбить.
 */
export function buildGroups(result: AnalysisResponse, catalog: DocumentTypesResponse | null): RequirementGroup[] {
  const type = catalog?.document_types.find((item) => item.code === result.document_type)
  let n = 0
  const groups: RequirementGroup[] = []

  const byRule = new Map<string, RequirementItem[]>()
  for (const field of result.field_views) {
    const value = typeof field.value === 'string' ? field.value : field.value ? 'есть' : NOT_FOUND
    const found = field.found && value !== NOT_FOUND
    const item: RequirementItem = {
      key: field.key,
      n: ++n,
      kind: 'field',
      label: field.label,
      value: found ? value : null,
      found,
      box: field.box,
      confidence: field.confidence,
      method: '',
    }
    const rule = field.rule || 'rule:?'
    byRule.set(rule, [...(byRule.get(rule) ?? []), item])
  }
  for (const [rule, items] of byRule) groups.push({ rule, title: 'Обязательные текстовые поля', items })

  for (const mark of result.mark_views) {
    const rule = type?.marks.find((item) => item.key === mark.key)?.rule ?? ''
    groups.push({
      rule,
      title: mark.label,
      items: [
        {
          key: mark.key,
          n: ++n,
          kind: 'mark',
          label: mark.label,
          value: mark.present ? 'Обнаружена' : null,
          found: mark.present,
          box: mark.box,
          confidence: mark.confidence,
          method: mark.method,
        },
      ],
    })
  }
  return groups
}

export function isImageFile(file: File | null): boolean {
  if (!file) return false
  return file.type.startsWith('image/') || /\.(png|jpe?g)$/i.test(file.name)
}
