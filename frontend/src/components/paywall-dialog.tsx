"use client"

import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

export function PaywallDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const router = useRouter()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create your account to continue</DialogTitle>
          <DialogDescription>
            Save your tailored CV, generate cover letters, and track jobs — free for one
            application. Your CV and match results carry over automatically.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="sm:justify-start">
          <Button onClick={() => router.push("/register")}>Create account</Button>
          <Button variant="outline" onClick={() => router.push("/login")}>
            Log in
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
