"use client"

import { usePathname } from "next/navigation"
import { useRequireAuth } from "@/hooks/use-require-auth"
import { Sidebar } from "@/components/modernist/sidebar"
import { Topbar } from "@/components/modernist/topbar"

function getBreadcrumb(pathname: string): string {
  if (pathname === "/dashboard") return "Workspace / Overview"
  if (pathname === "/dashboard/cvs") return "Workspace / CVs"
  if (pathname === "/dashboard/jobs") return "Workspace / Jobs"
  if (pathname === "/dashboard/job-feed") return "Workspace / Job feed"
  if (pathname === "/dashboard/matches") return "Workspace / Reports"
  if (pathname.startsWith("/dashboard/matches/")) return "Workspace / Reports / Report detail"
  if (pathname === "/dashboard/cover-letters") return "Workspace / Cover letters"
  if (pathname === "/dashboard/applications") return "Workspace / Applications"
  if (pathname === "/dashboard/new") return "Start / New match"
  if (pathname === "/dashboard/settings") return "Account / Settings & billing"
  if (pathname === "/dashboard/continue") return "Workspace / Continue"
  return "Workspace"
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { isReady } = useRequireAuth()
  const pathname = usePathname() ?? "/dashboard"

  if (!isReady) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", color: "var(--color-neutral-700)" }}>
        Loading…
      </div>
    )
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)" }}>
      <Sidebar />
      <main style={{ flex: 1, minWidth: 0 }}>
        <Topbar crumb={getBreadcrumb(pathname)} />
        {children}
      </main>
    </div>
  )
}
