/**
 * The one switch between the mock replay and the real API (MINH_PLAN §C1).
 * Mocks stay on unless VITE_USE_MOCKS is exactly "false", so a fresh checkout demos offline.
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== 'false'

/** Prefix for every §D endpoint; Vite proxies it to the API in dev. */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? '/api'
