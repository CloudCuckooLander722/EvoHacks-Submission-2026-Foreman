import { animate, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'

interface Props {
  value: number
  format: (n: number) => string
}

/**
 * A counter that glides to each new value instead of jumping. It writes to the DOM
 * directly so ~4 updates a second don't re-render the meter. Reduced motion: no glide.
 */
export function AnimatedNumber({ value, format }: Props) {
  const ref = useRef<HTMLSpanElement>(null)
  const shown = useRef(value)
  const reduce = useReducedMotion()
  // Rendered once; afterwards only the effect writes the text, so React never resets it.
  const [initial] = useState(() => format(value))

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (reduce) {
      shown.current = value
      el.textContent = format(value)
      return
    }
    const controls = animate(shown.current, value, {
      duration: 0.45,
      ease: 'easeOut',
      onUpdate: (v) => {
        shown.current = v
        el.textContent = format(v)
      },
    })
    return () => controls.stop()
  }, [value, format, reduce])

  return <span ref={ref}>{initial}</span>
}
