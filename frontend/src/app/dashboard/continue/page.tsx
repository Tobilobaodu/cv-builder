"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useTrialStore } from "@/store/trial-store"

export default function ContinueWhereYouLeftOffPage() {
  const router = useRouter()
  const cvId = useTrialStore((s) => s.cvId)
  const matchId = useTrialStore((s) => s.matchId)

  useEffect(() => {
    if (!cvId && !matchId) {
      router.replace("/dashboard")
    }
  }, [cvId, matchId, router])

  if (!cvId && !matchId) {
    return null
  }

  return (
    <div className="mx-auto flex max-w-md flex-col justify-center py-24 text-center">
      <Card>
        <CardHeader>
          <CardTitle>Welcome! Your trial is now saved</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-muted-foreground">
            Your CV and job match have been attached to your account — nothing to redo.
          </p>
          <Button onClick={() => router.push("/try/results")}>View your results</Button>
          <Button variant="outline" onClick={() => router.push("/dashboard")}>
            Go to dashboard
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
