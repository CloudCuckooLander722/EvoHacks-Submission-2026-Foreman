import { Hero } from './sections/Hero/Hero'
import { HowItWorks } from './sections/HowItWorks/HowItWorks'
import { LiveDemo } from './sections/LiveDemo/LiveDemo'

/** One long page (MINH_PLAN §C): Hero → How Foreman works → Live demo (+ results). */
export default function App() {
  return (
    <main>
      <Hero />
      <HowItWorks />
      <LiveDemo />
      <footer className="footer hint center">
        Foreman · EvoHacks 2026 · Jev via Vercel AI Gateway, fallback LLM DeepSeek V4 Flash on Fireworks
      </footer>
    </main>
  )
}
