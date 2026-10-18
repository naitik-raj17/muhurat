// Typed API client generated from the backend's OpenAPI schema (`npm run gen:api`).
import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

export type Schemas = components['schemas']
export type Problem = Schemas['ProblemDetail'] & { errors?: { loc: string[]; msg: string }[] }

const TOKEN_KEY = 'wedding-ai.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // Private mode / blocked storage: the session simply won't survive a reload.
  }
}

/** Thrown for any non-2xx response; carries the RFC 9457 problem details. */
export class ApiError extends Error {
  readonly status: number
  readonly problem: Problem

  constructor(problem: Problem) {
    super(problem.detail ?? problem.title)
    this.status = problem.status
    this.problem = problem
  }
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = getToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  onResponse({ response }) {
    if (response.status === 401 && getToken()) {
      setToken(null)
      window.dispatchEvent(new Event('wedding-ai:logout'))
    }
    return response
  },
}

export const api = createClient<paths>({ baseUrl: '' })
api.use(auth)

/** Unwrap an openapi-fetch result: return data or throw ApiError. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise
  if (error !== undefined || !response.ok) {
    const problem =
      error && typeof error === 'object' && 'title' in error
        ? (error as Problem)
        : {
            type: 'about:blank',
            title: response.statusText || 'Request failed',
            status: response.status,
          }
    throw new ApiError(problem)
  }
  return data as T
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const fields = err.problem.errors?.map((e) => `${e.loc.at(-1)}: ${e.msg}`).join('; ')
    return fields ? `${err.message} (${fields})` : err.message
  }
  return err instanceof Error ? err.message : String(err)
}
