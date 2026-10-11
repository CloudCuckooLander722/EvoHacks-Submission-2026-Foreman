import { USE_MOCKS } from '../config'
import { realApi } from './client'
import { createMockApi } from './mock'
import type { ForemanApi } from './types'

/** The backend the whole site uses; flip VITE_USE_MOCKS to switch. */
export const api: ForemanApi = USE_MOCKS ? createMockApi() : realApi
