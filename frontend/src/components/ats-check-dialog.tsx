"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { errorMessage, ApiError } from "@/lib/api"
import { triggerAtsCheck, getAtsCheck } from "@/lib/trial-api"
import { useJobPoll } from "@/hooks/use-job-poll"
import { ProgressBar } from "@/components/modernist/progress-bar"

/**
 * Product Extension #1 (ATS structural-readiness check) had zero dashboard
 * UI before this — GET /cvs/{cvId}/ats-check 404s until a check has run, so
 * this dialog handles both "no result yet, offer to run one" and "show the
 * existing result" in one place, using the same trigger -> useJobPoll ->
 * refetch pattern as every other async job in this codebase.
 */
export function AtsCheckDialog({ cvId, cvName }: { cvId: string; cvName: string }) {
  const [open, setOpen] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const { isCompleted, isFailed } = useJobPoll(jobId)

  const resultQuery = useQuery({
    queryKey: ["ats-check", cvId, isCompleted],
    queryFn: () => getAtsCheck(cvId),
    enabled: open,
    retry: false,
  })

  async function handleRun() {
    setIsStarting(true)
    try {
      const result = await triggerAtsCheck(cvId)
      setJobId(result.jobId)
    } catch (error) {
      toast.error(errorMessage(error, "Couldn't start the ATS check."))
    } finally {
      setIsStarting(false)
    }
  }

  const hasResult = !resultQuery.isError && !!resultQuery.data
  const notRunYet = resultQuery.isError && resultQuery.error instanceof ApiError && resultQuery.error.status === 404

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        ATS check
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>ATS readiness — {cvName}</DialogTitle>
          <DialogDescription>
            Structural checks an applicant-tracking system would run against this CV.
          </DialogDescription>
        </DialogHeader>

        {resultQuery.isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

        {jobId && !isCompleted && !isFailed && (
          <div className="flex flex-col items-start gap-2">
            <p className="text-sm text-muted-foreground">Running check…</p>
            <ProgressBar isActive width={200} expectedDurationMs={5000} />
          </div>
        )}

        {isFailed && <p className="text-sm text-destructive">The check failed. Please try again.</p>}

        {notRunYet && !jobId && (
          <div className="flex flex-col items-start gap-3">
            <p className="text-sm text-muted-foreground">No check has been run yet.</p>
            <Button size="sm" onClick={handleRun} disabled={isStarting}>
              {isStarting ? "Starting…" : "Run ATS check"}
            </Button>
          </div>
        )}

        {hasResult && resultQuery.data && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">Overall score</span>
              <Badge variant={resultQuery.data.overallScore >= 0.7 ? "default" : "secondary"}>
                {Math.round(resultQuery.data.overallScore * 100)}%
              </Badge>
            </div>
            <ul className="space-y-2">
              {resultQuery.data.checks.map((check, i) => (
                <li key={i} className="flex items-start justify-between gap-3 text-sm">
                  <div>
                    <p className="font-medium">{check.checkType}</p>
                    <p className="text-muted-foreground">{check.detail}</p>
                  </div>
                  <Badge variant={check.passed ? "default" : "destructive"}>
                    {check.passed ? "Pass" : check.severity}
                  </Badge>
                </li>
              ))}
            </ul>
            <Button size="sm" variant="outline" onClick={handleRun} disabled={isStarting}>
              {isStarting ? "Starting…" : "Re-run check"}
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
