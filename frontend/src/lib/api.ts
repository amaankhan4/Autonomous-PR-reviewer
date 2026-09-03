/**
 * Thin typed fetch wrapper.
 *
 * Responsibilities kept here so no component ever touches `fetch` directly:
 *  - attach the bearer token
 *  - transparently refresh an expired access token exactly once per request
 *  - unwrap the backend's `{ error: { code, message, details } }` envelope
 */

import type { TokenResponse } from './types'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
const ACCESS_KEY = 'apr.access_token'
const REFRESH_KEY = 'apr.refresh_token'

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  /** Field-level messages from a 422, keyed by field name. */
  get fieldErrors(): Record<string, string> {
    const fields = this.details.fields
    if (!Array.isArray(fields)) return {}
    const result: Record<string, string> = {}
    for (const entry of fields) {
      if (entry && typeof entry === 'object' && 'field' in entry && 'message' in entry) {
        result[String((entry as { field: unknown }).field)] = String(
          (entry as { message: unknown }).message,
        )
      }
    }
    return result
  }
}

export const tokenStore = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set(tokens: TokenResponse) {
    localStorage.setItem(ACCESS_KEY, tokens.access_token)
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

type Listener = () => void
const unauthorizedListeners = new Set<Listener>()

/** Notified when the session is definitively over, so the app can redirect. */
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

let refreshInFlight: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = tokenStore.refresh()
  if (!refreshToken) return false

  // Collapse concurrent 401s into a single refresh call.
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${BASE_URL}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      })
      if (!response.ok) return false
      tokenStore.set((await response.json()) as TokenResponse)
      return true
    } catch {
      return false
    } finally {
      // Release the lock on the next tick so waiters observe the result first.
      setTimeout(() => {
        refreshInFlight = null
      }, 0)
    }
  })()

  return refreshInFlight
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`
  let message = response.statusText || 'Request failed'
  let details: Record<string, unknown> = {}
  try {
    const body = await response.json()
    const error = body?.error
    if (error && typeof error === 'object') {
      code = String(error.code ?? code)
      message = String(error.message ?? message)
      details = (error.details ?? {}) as Record<string, unknown>
    }
  } catch {
    /* non-JSON error body: keep the status-derived message */
  }
  return new ApiError(response.status, code, message, details)
}

export interface RequestOptions {
  method?: string
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
  /** Skip the Authorization header (used by login/register). */
  anonymous?: boolean
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${BASE_URL}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal, anonymous = false } = options
  const url = buildUrl(path, query)

  const send = async (): Promise<Response> => {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const token = anonymous ? null : tokenStore.access()
    if (token) headers.Authorization = `Bearer ${token}`
    return fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  }

  let response = await send()

  if (response.status === 401 && !anonymous) {
    if (await refreshAccessToken()) {
      response = await send()
    }
    if (response.status === 401) {
      tokenStore.clear()
      unauthorizedListeners.forEach((listener) => listener())
      throw await toApiError(response)
    }
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  get: <T,>(path: string, query?: RequestOptions['query'], signal?: AbortSignal) =>
    request<T>(path, { query, signal }),
  post: <T,>(path: string, body?: unknown, query?: RequestOptions['query']) =>
    request<T>(path, { method: 'POST', body, query }),
  patch: <T,>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T,>(path: string) => request<T>(path, { method: 'DELETE' }),
  anonymousPost: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body, anonymous: true }),
}
