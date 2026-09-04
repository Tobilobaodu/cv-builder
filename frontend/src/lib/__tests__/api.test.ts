import { describe, expect, it } from "vitest"
import { http, HttpResponse } from "msw"
import { server } from "@/test/msw/server"
import { apiFetch, ApiError, errorMessage } from "@/lib/api"
import { useAuthStore } from "@/store/auth-store"
import { useTrialStore } from "@/store/trial-store"

const BASE = "http://localhost:8000/api/v1"

describe("apiFetch identity headers", () => {
  it("attaches Authorization when logged in", async () => {
    useAuthStore.setState({
      accessToken: "abc123",
      user: { id: "u1", email: "a@b.com" },
    })

    let receivedAuth: string | null = null
    let receivedTrial: string | null = null
    server.use(
      http.get(`${BASE}/echo`, ({ request }) => {
        receivedAuth = request.headers.get("Authorization")
        receivedTrial = request.headers.get("X-Trial-Session-Id")
        return HttpResponse.json({ ok: true })
      })
    )

    await apiFetch("/echo")
    expect(receivedAuth).toBe("Bearer abc123")
    expect(receivedTrial).toBeNull()
  })

  it("falls back to X-Trial-Session-Id when logged out", async () => {
    useTrialStore.setState({
      trialSessionId: "trial-1",
      expiresAt: new Date().toISOString(),
    })

    let receivedAuth: string | null = null
    let receivedTrial: string | null = null
    server.use(
      http.get(`${BASE}/echo`, ({ request }) => {
        receivedAuth = request.headers.get("Authorization")
        receivedTrial = request.headers.get("X-Trial-Session-Id")
        return HttpResponse.json({ ok: true })
      })
    )

    await apiFetch("/echo")
    expect(receivedAuth).toBeNull()
    expect(receivedTrial).toBe("trial-1")
  })

  it("prefers the bearer token over a trial session when both are present", async () => {
    useAuthStore.setState({
      accessToken: "abc123",
      user: { id: "u1", email: "a@b.com" },
    })
    useTrialStore.setState({
      trialSessionId: "trial-1",
      expiresAt: new Date().toISOString(),
    })

    let receivedAuth: string | null = null
    let receivedTrial: string | null = null
    server.use(
      http.get(`${BASE}/echo`, ({ request }) => {
        receivedAuth = request.headers.get("Authorization")
        receivedTrial = request.headers.get("X-Trial-Session-Id")
        return HttpResponse.json({ ok: true })
      })
    )

    await apiFetch("/echo")
    expect(receivedAuth).toBe("Bearer abc123")
    expect(receivedTrial).toBeNull()
  })

  it("sends neither header with no identity", async () => {
    let receivedAuth: string | null = null
    let receivedTrial: string | null = null
    server.use(
      http.get(`${BASE}/echo`, ({ request }) => {
        receivedAuth = request.headers.get("Authorization")
        receivedTrial = request.headers.get("X-Trial-Session-Id")
        return HttpResponse.json({ ok: true })
      })
    )

    await apiFetch("/echo")
    expect(receivedAuth).toBeNull()
    expect(receivedTrial).toBeNull()
  })

  it("clears the auth store on a 401 response", async () => {
    useAuthStore.setState({
      accessToken: "abc123",
      user: { id: "u1", email: "a@b.com" },
    })
    server.use(
      http.get(`${BASE}/echo`, () =>
        HttpResponse.json({ detail: "Unauthorized" }, { status: 401 })
      )
    )

    await expect(apiFetch("/echo")).rejects.toThrow(ApiError)
    expect(useAuthStore.getState().accessToken).toBeNull()
  })
})

// The "tab refresh logs me out" regression: a 401 used to destroy the
// session outright, so an access token the backend no longer accepted
// forced a re-login even though a valid 30-day refresh token was sitting
// in storage unused.
describe("apiFetch session renewal on 401", () => {
  function seedSession() {
    useAuthStore.setState({
      accessToken: "expired-token",
      refreshToken: "refresh-token-1",
      user: { id: "u1", email: "a@b.com" },
    })
  }

  it("renews the token and replays the request instead of signing the user out", async () => {
    seedSession()

    const sentTokens: (string | null)[] = []
    let calls = 0
    server.use(
      http.get(`${BASE}/echo`, ({ request }) => {
        sentTokens.push(request.headers.get("Authorization"))
        calls += 1
        if (calls === 1) {
          return HttpResponse.json({ detail: "Token has expired" }, { status: 401 })
        }
        return HttpResponse.json({ ok: true })
      })
    )

    await expect(apiFetch("/echo")).resolves.toEqual({ ok: true })

    // The retry carried the NEW token, not the rejected one.
    expect(sentTokens).toEqual([
      "Bearer expired-token",
      "Bearer refreshed-access-token",
    ])
    // Session survived, and the identity was not blanked by the renewal.
    expect(useAuthStore.getState().accessToken).toBe("refreshed-access-token")
    expect(useAuthStore.getState().user).toEqual({ id: "u1", email: "a@b.com" })
  })

  it("signs the user out when the refresh token itself is rejected", async () => {
    seedSession()
    server.use(
      http.get(`${BASE}/echo`, () =>
        HttpResponse.json({ detail: "Token has expired" }, { status: 401 })
      ),
      http.post(`${BASE}/auth/refresh`, () =>
        HttpResponse.json({ detail: "Refresh token is invalid." }, { status: 401 })
      )
    )

    await expect(apiFetch("/echo")).rejects.toThrow(ApiError)
    expect(useAuthStore.getState().accessToken).toBeNull()
    expect(useAuthStore.getState().refreshToken).toBeNull()
  })

  it("keeps the session when the refresh cannot be completed (rate limited)", async () => {
    seedSession()
    server.use(
      http.get(`${BASE}/echo`, () =>
        HttpResponse.json({ detail: "Token has expired" }, { status: 401 })
      ),
      http.post(`${BASE}/auth/refresh`, () =>
        HttpResponse.json({ detail: "Too many refresh attempts." }, { status: 429 })
      )
    )

    await expect(apiFetch("/echo")).rejects.toThrow(ApiError)
    // A throttled or briefly unreachable backend is not proof the
    // credentials are gone — the user stays signed in.
    expect(useAuthStore.getState().accessToken).toBe("expired-token")
    expect(useAuthStore.getState().refreshToken).toBe("refresh-token-1")
  })

  it("does not try to renew a failed login (a 401 there means bad credentials)", async () => {
    let refreshCalls = 0
    server.use(
      http.post(`${BASE}/auth/refresh`, () => {
        refreshCalls += 1
        return HttpResponse.json({ accessToken: "nope", refreshToken: "nope" })
      }),
      http.post(`${BASE}/auth/login`, () =>
        HttpResponse.json({ detail: "Invalid email or password." }, { status: 401 })
      )
    )
    useAuthStore.setState({ refreshToken: "refresh-token-1" })

    await expect(
      apiFetch("/auth/login", { method: "POST", body: { email: "a@b.com", password: "x" } })
    ).rejects.toThrow(ApiError)
    expect(refreshCalls).toBe(0)
  })

  it("redeems the refresh token only once for a burst of parallel 401s", async () => {
    seedSession()

    let refreshCalls = 0
    const expired = new Set<string>()
    server.use(
      http.post(`${BASE}/auth/refresh`, () => {
        refreshCalls += 1
        return HttpResponse.json({
          accessToken: "refreshed-access-token",
          refreshToken: "refresh-token-1",
        })
      }),
      http.get(`${BASE}/one`, ({ request }) => respond(request, "one")),
      http.get(`${BASE}/two`, ({ request }) => respond(request, "two")),
      http.get(`${BASE}/three`, ({ request }) => respond(request, "three"))
    )

    function respond(request: Request, name: string) {
      if (request.headers.get("Authorization") === "Bearer expired-token") {
        expired.add(name)
        return HttpResponse.json({ detail: "Token has expired" }, { status: 401 })
      }
      return HttpResponse.json({ name })
    }

    const results = await Promise.all([
      apiFetch("/one"),
      apiFetch("/two"),
      apiFetch("/three"),
    ])

    expect(expired.size).toBe(3)
    expect(results).toEqual([{ name: "one" }, { name: "two" }, { name: "three" }])
    // Every redemption supersedes the previous access token server-side, so
    // three concurrent refreshes would knock each other out.
    expect(refreshCalls).toBe(1)
  })
})

describe("apiFetch error and body handling", () => {
  it("throws ApiError with status and parsed body on a non-ok response", async () => {
    server.use(
      http.get(`${BASE}/echo`, () =>
        HttpResponse.json({ detail: "boom" }, { status: 409 })
      )
    )

    try {
      await apiFetch("/echo")
      expect.unreachable("apiFetch should have thrown")
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(409)
      expect((error as ApiError).body).toEqual({ detail: "boom" })
    }
  })

  it("sends FormData bodies without a Content-Type header (browser sets the boundary)", async () => {
    let receivedContentType: string | null = null
    server.use(
      http.post(`${BASE}/upload`, ({ request }) => {
        receivedContentType = request.headers.get("Content-Type")
        return HttpResponse.json({ ok: true })
      })
    )

    const formData = new FormData()
    formData.append("file", new Blob(["x"]), "test.pdf")
    await apiFetch("/upload", { method: "POST", body: formData })

    expect(receivedContentType).not.toBe("application/json")
  })
})

describe("errorMessage", () => {
  function apiError(status: number, body: unknown) {
    return new ApiError(status, body)
  }

  it("returns a string detail unchanged", () => {
    expect(
      errorMessage(apiError(409, { detail: "Already exists." }), "fallback")
    ).toBe("Already exists.")
  })

  it("extracts msg and field from a FastAPI 422 detail array", () => {
    const body = {
      detail: [
        {
          type: "string_too_short",
          loc: ["body", "password"],
          msg: "String should have at least 12 characters",
          ctx: { min_length: 12 },
        },
      ],
    }
    expect(errorMessage(apiError(422, body), "fallback")).toBe(
      "password: String should have at least 12 characters"
    )
  })

  it("joins multiple validation issues", () => {
    const body = {
      detail: [
        { loc: ["body", "email"], msg: "value is not a valid email address" },
        { loc: ["body", "password"], msg: "String should have at least 12 characters" },
      ],
    }
    expect(errorMessage(apiError(422, body), "fallback")).toBe(
      "email: value is not a valid email address " +
        "password: String should have at least 12 characters"
    )
  })

  it("omits the field prefix when loc carries no usable field name", () => {
    const body = { detail: [{ loc: ["body"], msg: "Invalid payload" }] }
    expect(errorMessage(apiError(422, body), "fallback")).toBe("Invalid payload")
  })

  it("falls back when detail is an empty array or has no usable msg", () => {
    expect(errorMessage(apiError(422, { detail: [] }), "fallback")).toBe("fallback")
    expect(
      errorMessage(apiError(422, { detail: [{ loc: ["body"] }] }), "fallback")
    ).toBe("fallback")
  })

  it("falls back for a non-ApiError or a body without detail", () => {
    expect(errorMessage(new Error("boom"), "fallback")).toBe("fallback")
    expect(errorMessage(apiError(500, { message: "nope" }), "fallback")).toBe(
      "fallback"
    )
  })
})
