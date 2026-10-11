import { motion, useReducedMotion } from 'framer-motion'
import { useEffect } from 'react'

interface Props {
  building: string
  hall: string
  /** где на картинке двери (доли ширины и высоты) — туда и «летит» камера */
  doors?: [number, number]
  onDone: () => void
}

const DURATION = 0.9

/**
 * «Заходим в здание»: камера наезжает на двери корпуса, потом проявляется холл.
 * Скипается кликом или клавишей; при prefers-reduced-motion вообще не показываем.
 */
export default function EnterTransition({ building, hall, doors = [0.625, 0.85], onDone }: Props) {
  const reduced = useReducedMotion()

  useEffect(() => {
    if (reduced) {
      onDone()
      return
    }
    const timer = window.setTimeout(onDone, (DURATION + 0.35) * 1000)
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
  const origin = `${doors[0] * 100}% ${doors[1] * 100}%`

  return (
    <div className="enter" onClick={onDone} role="presentation" title="Нажмите, чтобы пропустить">
      <motion.img
        className="enter__building"
        src={building}
        alt=""
        style={{ transformOrigin: origin }}
        initial={{ scale: 1, opacity: 1 }}
        animate={{ scale: 4.2, opacity: 0 }}
        transition={{ duration: DURATION, ease: [0.55, 0, 0.75, 0.3], opacity: { duration: 0.35, delay: DURATION - 0.3 } }}
      />
      <motion.img
        className="enter__hall"
        src={hall}
        alt=""
        initial={{ scale: 1.25, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.5, delay: DURATION - 0.35, ease: 'easeOut' }}
      />
      <span className="enter__skip">Нажмите, чтобы пропустить</span>
    </div>
  )
}
