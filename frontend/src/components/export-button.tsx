"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { errorMessage } from "@/lib/api"
import { getExport, downloadExport, type ExportRequestOut } from "@/lib/trial-api"
import { ProgressBar } from "@/components/modernist/progress-bar"

/**
 * Generalizes the create-export -> poll -> download flow proven in
 * try/results/page.tsx (Sprint 5's trial-accessible CV export) so the
 * dashboard's cover-letter/application-pack/PDF exports (Workstream D)
 * don't each reimplement the same polling/download-latch logic.
 */
export function ExportButton({
  label,
  startExport,
  filename,
  disabled,
  variant = "default",
}: {
  label: string
  startExport: () => Promise<ExportRequestOut>
  filename: string
  disabled?: boolean
  variant?: "default" | "outline" | "secondary"
}) {
  const [exportId, setExportId] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const [hasDownloaded, setHasDownloaded] = useState(false)

  const exportQuery = useQuery({
    queryKey: ["export", exportId],
    queryFn: () => getExport(exportId as string),
    enabled: !!exportId,
    refetchInterval: (q) => {
      const status = q.state.data?.status
      // Previously only stopped on "completed" — a failed export polled
      // forever at 1500ms since nothing else ever flips isBusy to false.
      return status === "completed" || status === "failed" ? false : 1500
    },
  })

  useEffect(() => {
    if (exportQuery.data?.status === "completed" && exportId && !hasDownloaded) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setHasDownloaded(true)
      downloadExport(exportId, filename).catch((error) => {
        toast.error(errorMessage(error, "Download failed. Please try again."))
      })
    }
    if (exportQuery.data?.status === "failed") {
      toast.error("Export failed. Please try again.")
    }
  }, [exportQuery.data?.status, exportId, hasDownloaded, filename])

  async function handleClick() {
    setIsStarting(true)
    try {
      const result = await startExport()
      setExportId(result.id)
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start the export."))
    } finally {
      setIsStarting(false)
    }
  }

  const isPolling = !!exportId && exportQuery.data?.status !== "completed" && exportQuery.data?.status !== "failed"
  const isBusy = isStarting || isPolling

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <Button
        size="sm"
        variant={variant}
        onClick={handleClick}
        disabled={disabled || isBusy || hasDownloaded}
      >
        {hasDownloaded ? "Downloaded" : isBusy ? "Preparing…" : label}
      </Button>
      <ProgressBar isActive={isPolling} width={60} height={6} expectedDurationMs={6000} />
    </div>
  )
}
