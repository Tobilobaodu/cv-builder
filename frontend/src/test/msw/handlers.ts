import { http, HttpResponse } from "msw"

const API_BASE_URL = "http://localhost:8000/api/v1"

export const registerHandler = http.post(`${API_BASE_URL}/auth/register`, async () => {
  return HttpResponse.json(
    {
      id: "user-1",
      email: "test@example.com",
      accountStatus: "active",
      createdAt: new Date().toISOString(),
    },
    { status: 201 }
  )
})

export const loginHandler = http.post(`${API_BASE_URL}/auth/login`, async () => {
  return HttpResponse.json({
    accessToken: "test-access-token",
    refreshToken: "test-refresh-token",
    user: {
      id: "user-1",
      email: "test@example.com",
      accountStatus: "active",
      createdAt: new Date().toISOString(),
    },
  })
})

export const loginInvalidHandler = http.post(`${API_BASE_URL}/auth/login`, async () => {
  return HttpResponse.json({ detail: "Invalid email or password." }, { status: 401 })
})

// lib/api.ts redeems the stored refresh token whenever a request 401s, so
// any test whose handler returns a 401 while a refreshToken is in the store
// reaches this endpoint too. A default handler keeps those tests from
// tripping MSW's onUnhandledRequest: "error" policy (test/setup.ts).
export const refreshHandler = http.post(`${API_BASE_URL}/auth/refresh`, async () => {
  return HttpResponse.json({
    accessToken: "refreshed-access-token",
    refreshToken: "test-refresh-token",
    user: {
      id: "user-1",
      email: "test@example.com",
      accountStatus: "active",
      createdAt: new Date().toISOString(),
    },
  })
})

// performLogout() (lib/auth-api.ts) always fires this before clearing local
// auth state — a default handler here means any test exercising logout
// (Navbar, Topbar) doesn't need to mock it just to satisfy MSW's
// onUnhandledRequest: "error" policy (test/setup.ts).
export const logoutHandler = http.post(
  `${API_BASE_URL}/auth/logout`,
  () => new HttpResponse(null, { status: 204 })
)

export const registerConflictHandler = http.post(`${API_BASE_URL}/auth/register`, async () => {
  return HttpResponse.json(
    { detail: "An account with this email already exists." },
    { status: 409 }
  )
})

// Fire-and-forget journey-latency beacon (jbs-solution-sheet.md O4) —
// hit from any page that instruments a journey (currently /try/upload).
// A default handler here means individual page tests don't need to mock
// it just to silence MSW's unhandled-request warning.
export const journeyBeaconHandler = http.post(
  `${API_BASE_URL}/client-metrics/journey`,
  () => new HttpResponse(null, { status: 204 })
)

export const handlers = [
  registerHandler,
  loginHandler,
  logoutHandler,
  refreshHandler,
  journeyBeaconHandler,
]
