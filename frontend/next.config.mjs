import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {};

// Wrapping is what uploads source maps at build time — without it, stack
// traces in the dashboard point at minified bundles and are unreadable.
// SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT and SENTRY_URL are all
// injected by Temps into the build, so no credential is configured here; the
// upload step no-ops wherever they are absent, such as a local build.
export default withSentryConfig(nextConfig, {
  // The plugin's own build chatter, not the app's. Errors still surface.
  silent: true,
  // Source maps are uploaded for the dashboard, then deleted from the output
  // so the running server never serves the original sources to the public.
  widenClientFileUpload: true,
  sourcemaps: { deleteSourcemapsAfterUpload: true },
});
