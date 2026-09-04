import { useQuery } from "@tanstack/react-query"

const POLL_INTERVAL_MIN_MS = 2000
const POLL_INTERVAL_MAX_MS = 15000
// ~2 minutes of backoff (2s, 4s, 8s, 15s, 15s, ...) before giving up.
const MAX_ATTEMPTS = 12

/**
 * Polls a resource that 404s until some other async pipeline stage finishes
 * writing it (e.g. GET /cvs/{id}/parsed-profile, which only resolves once
 * the cv_analyze job — chained after text_extract — completes). Same 2s->15s
 * backoff as useJobPoll, capped at MAX_ATTEMPTS so a genuinely failed
 * upstream job surfaces as "timed out" instead of polling forever.
 */
export function usePollUntilReady<T>(
  queryKey: unknown[],
  queryFn: () => Promise<T>,
  enabled: boolean
) {
  const query = useQuery<T>({
    queryKey,
    queryFn,
    enabled,
    retry: false,
    refetchInterval: (q) => {
      if (q.state.data !== undefined) return false
      if (q.state.fetchFailureCount >= MAX_ATTEMPTS) return false
      return Math.min(POLL_INTERVAL_MIN_MS * 2 ** q.state.fetchFailureCount, POLL_INTERVAL_MAX_MS)
    },
  })

  const isTimedOut = enabled && query.data === undefined && query.failureCount >= MAX_ATTEMPTS

  return {
    data: query.data,
    isReady: query.data !== undefined,
    isTimedOut,
    error: query.error,
  }
}
