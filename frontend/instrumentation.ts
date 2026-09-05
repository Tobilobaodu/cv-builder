// Server- and edge-side error tracking.
//
// Next calls register() once per runtime before any application code runs,
// which is the only point early enough to catch errors thrown during startup.
// SENTRY_DSN and SENTRY_RELEASE arrive from Temps on every deployment, and the
// SDK reads both from the environment, so no DSN or release is written here.
//
// This replaces an earlier instrumentation.ts that imported '@vercel/otel' —
// a package that was never in package.json, so the file broke every
// production build from the first commit until it was removed. Traces now come
// from the Sentry SDK, which is already present for error reporting.
import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (!process.env.SENTRY_DSN) return;

  Sentry.init({
    // CVs, cover letters and auth tokens pass through this app, so request
    // headers, cookies and bodies are never attached to an event.
    sendDefaultPii: false,
    // Every transaction is sampled; lower this before real traffic arrives.
    tracesSampleRate: 1.0,
  });
}

// Next hands nested React Server Component errors to this hook; without it
// they are swallowed rather than reported.
export const onRequestError = Sentry.captureRequestError;
