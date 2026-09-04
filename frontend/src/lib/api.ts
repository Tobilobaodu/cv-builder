import { toast } from "sonner"

import { useAuthStore } from "@/store/auth-store"
import { useTrialStore } from "@/store/trial-store"

/** Clears auth state on a 401, and — only when there actually was a
 *  session to lose — tells the user why they were signed out. Checking
 *  accessToken !== null *before* clearing distinguishes "your session
 *  just died" from "you were never logged in" (e.g. a failed /auth/login
 *  attempt, which also 401s but has nothing to do with session expiry).
 */
function handleUnauthorized() {
  const hadSession = useAuthStore.getState().accessToken !== null
  useAuthStore.getState().clearAuth()
  if (hadSession) {
    toast.error("Your session has expired — please sign in again.")
  }
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"

const REFRESH_PATH = "/auth/refresh"

/** Endpoints that must never trigger the refresh-and-retry path below.
 *  A 401 from /auth/login is "wrong password", not "expired session", and
 *  a 401 from /auth/refresh is the refresh itself failing — retrying
 *  either would be wrong, and recursing into /auth/refresh would loop. */
const NON_RENEWABLE_PATHS = ["/auth/login", "/auth/register", REFRESH_PATH]

/** Outcome of trying to renew the session after a 401.
 *  - "renewed"   — new access token stored; the caller should retry once.
 *  - "dead"      — the refresh token itself was rejected; sign the user out.
 *  - "transient" — refresh could not be completed (network, 429, 5xx). The
 *                  session is deliberately LEFT INTACT: a rate-limited or
 *                  briefly unreachable backend is not proof that the
 *                  user's credentials are gone, and clearing here is what
 *                  turned a momentary blip into a surprise logout.
 *  - "skipped"   — not eligible (no refresh token, or a non-renewable
 *                  path), so the legacy behaviour applies. */
type RenewalOutcome = "renewed" | "dead" | "transient" | "skipped"

async function requestNewAccessToken(): Promise<RenewalOutcome> {
  const refreshToken = useAuthStore.getState().refreshToken
  if (refreshToken === null) return "skipped"

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${REFRESH_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refreshToken }),
    })
  } catch {
    return "transient"
  }

  if (response.status === 401 || response.status === 403) return "dead"
  if (!response.ok) return "transient"

  try {
    const data = (await response.json()) as {
      accessToken?: unknown
      refreshToken?: unknown
    }
    if (typeof data.accessToken !== "string") return "transient"
    useAuthStore
      .getState()
      .setTokens(
        data.accessToken,
        typeof data.refreshToken === "string" ? data.refreshToken : refreshToken
      )
    return "renewed"
  } catch {
    return "transient"
  }
}

/** Single-flight: the dashboard fires several authenticated queries in
 *  parallel, so an expired token produces a burst of simultaneous 401s.
 *  Without sharing one in-flight refresh they would each redeem the token
 *  separately, and every redemption supersedes the previous access token —
 *  the requests would knock each other's credentials out and at least one
 *  would still fail. */
let renewalInFlight: Promise<RenewalOutcome> | null = null

function renewSessionOnce(path: string): Promise<RenewalOutcome> {
  if (NON_RENEWABLE_PATHS.some((p) => path.startsWith(p))) {
    return Promise.resolve("skipped")
  }
  if (renewalInFlight === null) {
    renewalInFlight = requestNewAccessToken()
    void renewalInFlight.finally(() => {
      renewalInFlight = null
    })
  }
  return renewalInFlight
}

/** Shared 401 policy for all three request helpers. Returns true when the
 *  caller should replay its request with the freshly stored token. */
async function shouldRetryAfter401(path: string): Promise<boolean> {
  const outcome = await renewSessionOnce(path)
  if (outcome === "renewed") return true
  // "transient" keeps the session so the user can carry on once the
  // backend recovers; the original error still surfaces to the caller.
  if (outcome === "dead" || outcome === "skipped") handleUnauthorized()
  return false
}

export class ApiError extends Error {
  status: number
  body: unknown

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `Request failed with status ${status}`)
    this.name = "ApiError"
    this.status = status
    this.body = body
  }
}

type ApiFetchOptions = Omit<RequestInit, "body"> & {
  body?: unknown
}

/**
 * Sends the account's bearer token when logged in, otherwise falls back to
 * the anonymous trial session header — never both (mirrors the backend's
 * own precedence in get_current_user_or_trial_session).
 */
function buildIdentityHeaders(): Record<string, string> {
  const accessToken = useAuthStore.getState().accessToken
  if (accessToken) {
    return { Authorization: `Bearer ${accessToken}` }
  }

  const trialSessionId = useTrialStore.getState().trialSessionId
  if (trialSessionId) {
    return { "X-Trial-Session-Id": trialSessionId }
  }

  return {}
}

export async function apiFetch<T>(
  path: string,
  options: ApiFetchOptions = {}
): Promise<T> {
  const { body, headers, ...rest } = options
  const isFormData = body instanceof FormData

  // Re-reads the identity headers on every call so a replay after a
  // successful refresh picks up the NEW token rather than resending the
  // rejected one.
  const send = () =>
    fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...buildIdentityHeaders(),
        ...headers,
      },
      body: isFormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
    })

  let response = await send()

  if (response.status === 401 && (await shouldRetryAfter401(path))) {
    response = await send()
    // Still 401 with a token the backend just issued: the session is not
    // recoverable, so fall through to the sign-out path.
    if (response.status === 401) handleUnauthorized()
  }

  if (!response.ok) {
    let parsedBody: unknown = null
    try {
      parsedBody = await response.json()
    } catch {
      // response had no JSON body
    }
    throw new ApiError(response.status, parsedBody)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

/** Like apiFetch, but for binary responses (e.g. file downloads) — returns a Blob instead of parsing JSON.
 *  Accepts a body so a download can be produced by a POST, which the PDF
 *  export needs: the rewrite is stateless, so the Markdown to render is
 *  sent with the request rather than referenced by id. */
export async function apiFetchBlob(
  path: string,
  options: ApiFetchOptions = {}
): Promise<Blob> {
  const { body, headers, ...rest } = options
  const isFormData = body instanceof FormData

  const send = () =>
    fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers: {
        ...(isFormData || body === undefined
          ? {}
          : { "Content-Type": "application/json" }),
        ...buildIdentityHeaders(),
        ...headers,
      },
      body: isFormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
    })

  let response = await send()

  if (response.status === 401 && (await shouldRetryAfter401(path))) {
    response = await send()
    if (response.status === 401) handleUnauthorized()
  }

  if (!response.ok) {
    let parsedBody: unknown = null
    try {
      parsedBody = await response.json()
    } catch {
      // response had no JSON body
    }
    throw new ApiError(response.status, parsedBody)
  }

  return await response.blob()
}

/** Like apiFetch, but for a streamed (text/event-stream) response — yields
 *  each SSE `data:` frame's raw text as it arrives, parsed as JSON by the
 *  caller (this function doesn't know the event shape, only the SSE
 *  framing: `data: <text>` lines separated by a blank line).
 *
 *  A generator rather than a callback list — `for await` at the call site
 *  reads naturally and composes with try/catch for the error path, where
 *  a callback-based API would need its own onError plumbing. */
export async function* apiFetchStream(
  path: string,
  options: ApiFetchOptions = {}
): AsyncGenerator<string> {
  const { body, headers, ...rest } = options

  const send = () =>
    fetch(`${API_BASE_URL}${path}`, {
      ...rest,
      headers: {
        "Content-Type": "application/json",
        ...buildIdentityHeaders(),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })

  let response = await send()

  if (response.status === 401 && (await shouldRetryAfter401(path))) {
    response = await send()
    if (response.status === 401) handleUnauthorized()
  }

  if (!response.ok || !response.body) {
    let parsedBody: unknown = null
    try {
      parsedBody = await response.json()
    } catch {
      // response had no JSON body
    }
    throw new ApiError(response.status, parsedBody)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let frameEnd: number
      // eslint-disable-next-line no-cond-assign
      while ((frameEnd = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, frameEnd)
        buffer = buffer.slice(frameEnd + 2)
        const line = frame.split("\n").find((l) => l.startsWith("data: "))
        if (line) yield line.slice("data: ".length)
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** FastAPI puts an *array* of issue objects in `detail` for a 422 validation
 *  error, not a string:
 *    { detail: [{ loc: ["body","password"], msg: "String should have at
 *                 least 12 characters", type: "string_too_short" }] }
 *  Without this branch every 422 fell through to the caller's generic
 *  fallback. That is how the frontend/backend password-length mismatch
 *  stayed invisible: the real reason was in the response body and the user
 *  only ever saw "Could not create your account."
 *
 *  The field name is taken from the tail of `loc` ("body" dropped), because
 *  a bare "String should have at least 12 characters" does not say which
 *  input is wrong. */
function validationDetailMessage(detail: unknown): string | null {
  if (!Array.isArray(detail) || detail.length === 0) {
    return null
  }

  const messages: string[] = []
  for (const issue of detail) {
    if (!issue || typeof issue !== "object") continue
    const { msg, loc } = issue as { msg?: unknown; loc?: unknown }
    if (typeof msg !== "string") continue

    let field: string | undefined
    if (Array.isArray(loc)) {
      const parts = loc.filter(
        (part): part is string => typeof part === "string" && part !== "body"
      )
      field = parts.length > 0 ? parts[parts.length - 1] : undefined
    }

    messages.push(field ? `${field}: ${msg}` : msg)
  }

  return messages.length > 0 ? messages.join(" ") : null
}

/** Extracts a human-readable message from the backend's HTTPException body shape ({"detail": "..."}). */
export function errorMessage(error: unknown, fallback: string): string {
  if (
    error instanceof ApiError &&
    error.body &&
    typeof error.body === "object" &&
    "detail" in error.body
  ) {
    const { detail } = error.body as { detail?: unknown }
    if (typeof detail === "string") {
      return detail
    }
    const validationMessage = validationDetailMessage(detail)
    if (validationMessage !== null) {
      return validationMessage
    }
  }
  return fallback
}

// ── Journey latency beacon (jbs-solution-sheet.md O4) ──
// The server can't see poll lag or render time on its own — S5's 14s of
// dead time lived entirely there. Fire-and-forget: never awaited by the
// caller, never surfaces an error to the user — losing a metrics beacon
// must not affect the product. Lives here (not trial-api.ts, which
// re-exports it) since both the trial and dashboard flows fire it.
export function recordJourney(journey: string, durationSeconds: number) {
  void apiFetch("/client-metrics/journey", {
    method: "POST",
    body: { journey, durationSeconds },
  }).catch(() => {
    // best-effort telemetry only
  })
}
