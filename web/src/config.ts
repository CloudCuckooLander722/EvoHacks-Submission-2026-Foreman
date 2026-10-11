/**
 * The one switch between the mock replay and the real API (MINH_PLAN §C1).
 * The site uses the live API when /health answers, else replay mode. VITE_USE_MOCKS=true forces
 * replay only (a static deploy with no API behind it).
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true'

/** Prefix for every §D endpoint; Vite proxies it to the API in dev. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'
