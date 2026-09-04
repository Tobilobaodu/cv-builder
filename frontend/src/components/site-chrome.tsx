"use client"

import { usePathname } from "next/navigation"
import { Navbar } from "@/components/navbar"
import { Footer } from "@/components/footer"

/**
 * The dashboard owns a full-bleed 268px sidebar + topbar shell (see
 * app/dashboard/layout.tsx) and must not also render the marketing
 * Navbar/Footer above it. Every other route (marketing home, auth, the
 * anonymous /try/* trial flow) keeps the global chrome, just restyled to
 * Modernist. A client-side pathname check here was chosen over splitting
 * the tree into multiple route-group root layouts (see AGENTS.md's Next 16
 * routing note) — this app has a single root layout, so there is no
 * "navigating between root layouts" full-reload caveat to worry about, and
 * this keeps every route's file path — and every test's import path —
 * unchanged.
 */
export function SiteChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const isDashboard = pathname?.startsWith("/dashboard") ?? false

  if (isDashboard) {
    return <>{children}</>
  }

  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  )
}
