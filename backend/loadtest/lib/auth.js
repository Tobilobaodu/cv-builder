import http from "k6/http";
import { check } from "k6";

/**
 * Logs in a pre-seeded test account and returns a bearer token.
 *
 * Deliberately does NOT call /register per-VU — creating N users on every
 * test run pollutes the target database exactly the way the unisolated
 * host-venv pytest runs used to (see backend/tests/conftest.py). Load
 * tests should run against a seeded database of test accounts instead;
 * see ../README.md#seeding for the expected account naming convention.
 */
export function login(baseUrl, email, password) {
  const res = http.post(
    `${baseUrl}/api/v1/auth/login`,
    JSON.stringify({ email, password }),
    { headers: { "Content-Type": "application/json" }, tags: { endpoint: "login" } },
  );
  check(res, { "login succeeded": (r) => r.status === 200 });
  if (res.status !== 200) {
    return null;
  }
  return res.json("accessToken");
}

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
}
