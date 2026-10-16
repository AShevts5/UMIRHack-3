import { motion, useReducedMotion } from 'framer-motion'
import { useEffect } from 'react'

/** Где на экране входа стояла открытка с корпусом: отсюда и стартуем. */
export interface PostcardRect {
  left: number
  top: number
  width: number
  height: number
  rotate: number
}

interface Props {
  building: string
  /** где на картинке двери (доли ширины и высоты) — туда и «летит» камера */
  doors?: [number, number]
  /** открытка с экрана входа; если её не было (вернулись из Яндекса), начинаем сразу с полного экрана */
  from?: PostcardRect | null
  onDone: () => void
}

// картинки корпусов у нас все 800×1000, на этом и считаем, где двери после object-fit: cover
const BUILDING_ASPECT = 800 / 1000
// у открытки object-position 50% 70%, а на весь экран прижимаем картинку к низу (50% 100%):
// иначе на широком экране двери уезжают за нижний край и камере некуда лететь
const OBJECT_Y = 1

const GROW = 0.45 // открытка разворачивается на весь экран
const ZOOM_AT = 0.3 // и почти сразу камера едет в двери
const ZOOM = 0.8
const REVEAL_AT = 0.95 // тут всё гаснет и под ним проявляется настоящий холл
const REVEAL = 0.4
const TOTAL = REVEAL_AT + REVEAL + 0.1

/** Где двери на весь экран (картинку обрезает cover): в пикселях от левого верхнего угла. */
function doorsOnScreen(doors: [number, number]): { x: number; y: number } {
  const w = window.innerWidth
  const h = window.innerHeight
  const imgH = Math.max(w / BUILDING_ASPECT, h)
  const imgW = imgH * BUILDING_ASPECT
  return { x: (w - imgW) * 0.5 + doors[0] * imgW, y: (h - imgH) * OBJECT_Y + doors[1] * imgH }
}

/**
 * «Заходим в здание»: открытка с корпусом разворачивается на весь экран, камера
 * наезжает на двери, и всё это тает, открывая уже настоящий холл. Отдельной картинки
 * холла тут больше нет: раньше она не совпадала со страницей и в конце всё прыгало.
 * Скипается кликом или клавишей; при prefers-reduced-motion вообще не показываем.
 */
export default function EnterTransition({ building, doors = [0.625, 0.85], from, onDone }: Props) {
  const reduced = useReducedMotion()

  useEffect(() => {
    if (reduced) {
      onDone()
      return
    }
    const timer = window.setTimeout(onDone, TOTAL * 1000)
    const skip = (event: KeyboardEvent) => {
      if (event.key === 'Escape' || event.key === 'Enter' || event.key === ' ') onDone()
    }
    window.addEventListener('keydown', skip)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('keydown', skip)
    }
  }, [onDone, reduced])

  if (reduced) return null
  const full = { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight, rotate: 0 }
  // на широком экране двери оказываются у самого низа, поэтому камера не только приближает,
  // но и подтягивает двери в центр кадра, иначе «влетаем» в окна второго этажа
  const door = doorsOnScreen(doors)

  return (
    <div className="enter" onClick={onDone} role="presentation" title="Нажмите, чтобы пропустить">
      <motion.div
        className="enter__backdrop"
        initial={{ opacity: 1 }}
        animate={{ opacity: 0 }}
        transition={{ delay: REVEAL_AT, duration: REVEAL, ease: 'easeOut' }}
      />
      <motion.div
        className="enter__frame"
        initial={{ ...(from ?? full), borderRadius: from ? 28 : 0, borderWidth: from ? 6 : 0, opacity: 1 }}
        animate={{ ...full, borderRadius: 0, borderWidth: 0, opacity: 0 }}
        transition={{ duration: GROW, ease: [0.3, 0, 0.2, 1], opacity: { delay: REVEAL_AT, duration: REVEAL * 0.75 } }}
      >
        <motion.img
          src={building}
          alt=""
          style={{ transformOrigin: `${door.x}px ${door.y}px` }}
          initial={{ scale: 1, x: 0, y: 0, objectPosition: from ? '50% 70%' : '50% 100%' }}
          animate={{ scale: 4.2, x: full.width / 2 - door.x, y: full.height * 0.55 - door.y, objectPosition: '50% 100%' }}
          transition={{
            delay: ZOOM_AT,
            duration: ZOOM,
            ease: [0.55, 0, 0.75, 0.3],
            objectPosition: { duration: GROW, ease: [0.3, 0, 0.2, 1] },
          }}
        />
      </motion.div>
      <span className="enter__skip">Нажмите, чтобы пропустить</span>
    </div>
  )
}
