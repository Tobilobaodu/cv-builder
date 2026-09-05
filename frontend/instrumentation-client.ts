// Browser-side error tracking and tracing.
//
// Temps is Sentry wire-compatible and injects NEXT_PUBLIC_SENTRY_DSN into the
// build as both a Docker build-arg and a runtime variable, so the value is
// inlined into the client bundle without a DSN ever being written down here.
// The public key is write-only ingestion credentials, safe to ship to browsers
// — unlike the dt_/tk_ tokens, which must never reach client code.
//
// Nothing initialises when the variable is absent, which is the case for a
// plain `npm run dev`, so local work does not report into the deployed project.
import * as Sentry from "@sentry/nextjs";

if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
  Sentry.init({
    dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
    // Routes ingestion back through this app's own origin. Temps supplies the
    // path; without it, ad-blockers drop a meaningful share of error reports.
    tunnel: process.env.NEXT_PUBLIC_SENTRY_TUNNEL,
    // CVs, cover letters and auth tokens all pass through this UI, so no
    // request headers, cookies or bodies are attached to events.
    sendDefaultPii: false,
    // Every transaction is sampled. Appropriate while this is a single-user
    // deployment; lower it before the app carries real traffic.
    tracesSampleRate: 1.0,
    // release is intentionally unset — the SDK reads SENTRY_RELEASE, which
    // Temps sets to the deployment's commit SHA. Hardcoding it would break
    // the mapping from a stack frame back to its source.
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
