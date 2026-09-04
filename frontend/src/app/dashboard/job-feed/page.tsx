"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { importJobFeedPosting, listJobFeed } from "@/lib/job-feed-api"
import { errorMessage } from "@/lib/api"
import { Tag } from "@/components/modernist/tag"
import { TableShell } from "@/components/modernist/table-shell"

const SOURCE_LABEL: Record<string, string> = {
  remoteok: "RemoteOK",
  remotive: "Remotive",
  arbeitnow: "Arbeitnow",
  reed: "Reed",
  usajobs: "USAJobs",
}

// Same grow-the-limit pagination the Jobs page uses (dashboard/jobs/page.tsx):
// one query whose limit grows, rather than accumulating pages client-side.
const PAGE_SIZE = 20
// GET /job-feed caps `limit` at 100 (app/api/v1/job_feed.py), exactly as
// GET /job-posts does. Requesting more is a 422, so the button stops at the
// ceiling and the note below explains how to reach older listings.
const MAX_ITEMS = 100

export default function JobFeedPage() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const [q, setQ] = useState("")
  const [appliedQ, setAppliedQ] = useState("")
  const [source, setSource] = useState("")
  const [remoteOnly, setRemoteOnly] = useState(false)
  const [importingId, setImportingId] = useState<string | null>(null)
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  const query = useQuery({
    queryKey: ["job-feed", appliedQ, source, remoteOnly, visibleCount],
    queryFn: () =>
      listJobFeed({
        q: appliedQ || undefined,
        source: source || undefined,
        remote: remoteOnly ? true : undefined,
        limit: visibleCount,
      }),
    // Growing `visibleCount` changes the query key, so without this the new
    // page counts as a fresh query: `isLoading` flips true, TableShell swaps
    // the rows for skeletons, and the button below unmounts mid-click (its
    // "Loading…" state would never be reachable). Keeping the previous page
    // as placeholder data means the loaded rows stay put and only the button
    // shows the pending state.
    placeholderData: (previousData) => previousData,
  })

  /** Any filter change starts a new result set — keep the page size from
   *  carrying over, so a search after several "Load more" clicks doesn't
   *  silently request 100 rows of the new query. */
  function applySearch() {
    setAppliedQ(q.trim())
    setVisibleCount(PAGE_SIZE)
  }

  async function handleImport(feedPostingId: string) {
    setImportingId(feedPostingId)
    try {
      await importJobFeedPosting(feedPostingId)
      toast.success("Added to your jobs — structuring now.")
      void queryClient.invalidateQueries({ queryKey: ["dashboard-job-posts"] })
      router.push("/dashboard/jobs")
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't import that listing."))
    } finally {
      setImportingId(null)
    }
  }

  const items = query.data?.items ?? []

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1100 }}>
      <div>
        <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>JOB FEED</h1>
        <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>
          Live listings from RemoteOK, Remotive, Arbeitnow, Reed and USAJobs — import one to tailor your CV for it.
        </p>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label>Search</label>
          <input
            className="input"
            placeholder="Title or company…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applySearch()
            }}
          />
        </div>
        <div className="field">
          <label>Source</label>
          <select
            className="input"
            value={source}
            onChange={(e) => {
              setSource(e.target.value)
              setVisibleCount(PAGE_SIZE)
            }}
          >
            <option value="">All sources</option>
            {Object.entries(SOURCE_LABEL).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ justifyContent: "flex-end" }}>
          <label>&nbsp;</label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14, height: 38 }}>
            <input
              type="checkbox"
              checked={remoteOnly}
              onChange={(e) => {
                setRemoteOnly(e.target.checked)
                setVisibleCount(PAGE_SIZE)
              }}
              style={{ accentColor: "var(--color-accent)", width: 15, height: 15 }}
            />
            Remote only
          </label>
        </div>
        <div className="field" style={{ justifyContent: "flex-end" }}>
          <label>&nbsp;</label>
          <button type="button" className="btn btn-secondary" onClick={applySearch}>
            Search
          </button>
        </div>
      </div>

      <TableShell
        isLoading={query.isLoading}
        isError={query.isError}
        isEmpty={!!query.data && query.data.items.length === 0}
        emptyMessage="No listings match yet — try a broader search, or check back after the next refresh."
      >
        <thead>
          <tr>
            <th>Role</th>
            <th>Employer</th>
            <th>Location</th>
            <th style={{ width: 100 }}>Source</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((posting) => (
            <tr key={posting.id}>
              <td style={{ fontWeight: 600 }}>
                <a href={posting.url} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>
                  {posting.title}
                </a>
              </td>
              <td>{posting.company ?? "—"}</td>
              <td>
                {posting.location ?? "—"}
                {posting.remote && (
                  <Tag variant="outline" style={{ marginLeft: 8 }}>
                    Remote
                  </Tag>
                )}
              </td>
              <td>
                <Tag variant="neutral">{SOURCE_LABEL[posting.source] ?? posting.source}</Tag>
              </td>
              <td style={{ textAlign: "right" }}>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={importingId === posting.id}
                  onClick={() => handleImport(posting.id)}
                >
                  {importingId === posting.id ? "Importing…" : "Import & tailor"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </TableShell>

      {query.data && query.data.total > items.length && visibleCount < MAX_ITEMS && (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={query.isFetching}
            onClick={() => setVisibleCount((n) => Math.min(n + PAGE_SIZE, MAX_ITEMS))}
          >
            {query.isFetching ? "Loading…" : "Load more"}
          </button>
        </div>
      )}

      {query.data && query.data.total > items.length && visibleCount >= MAX_ITEMS && (
        <p style={{ margin: 0, textAlign: "center", fontSize: 13, color: "var(--color-neutral-700)" }}>
          Showing the first {items.length} of {query.data.total} listings — narrow your search to see
          more.
        </p>
      )}
    </div>
  )
}
