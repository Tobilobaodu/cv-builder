"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { TrialWidget } from "@/components/modernist/trial-widget"

type NavLink = { href: string; label: string }

const workspaceLinks: NavLink[] = [
  { href: "/dashboard", label: "Overview" },
  { href: "/dashboard/cvs", label: "CVs" },
  { href: "/dashboard/jobs", label: "Jobs" },
  { href: "/dashboard/job-feed", label: "Job feed" },
  { href: "/dashboard/matches", label: "Reports" },
  { href: "/dashboard/cover-letters", label: "Cover letters" },
  { href: "/dashboard/applications", label: "Applications" },
]

const startLinks: NavLink[] = [{ href: "/dashboard/new", label: "New match" }]

const accountLinks: NavLink[] = [{ href: "/dashboard/settings", label: "Settings & billing" }]

/** Mirrors dashboard-nav.tsx's original active-state rule: the Overview
 *  link is only active on the exact /dashboard path, everything else on
 *  prefix match, so /dashboard/cvs doesn't also light up Overview. */
function isActive(pathname: string, href: string) {
  if (href === "/dashboard") return pathname === href
  return pathname.startsWith(href)
}

function NavGroup({ title, links, pathname }: { title: string; links: NavLink[]; pathname: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <div
        style={{
          fontSize: 11,
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          color: "var(--color-neutral-600)",
          padding: "0 8px 8px",
        }}
      >
        {title}
      </div>
      {links.map((link) => {
        const active = isActive(pathname, link.href)
        return (
          <Link
            key={link.href}
            href={link.href}
            style={{
              textAlign: "left",
              font: "inherit",
              fontSize: 14,
              border: 0,
              background: active ? "var(--color-text)" : "transparent",
              color: active ? "var(--color-bg)" : "inherit",
              padding: "9px 8px",
              cursor: "pointer",
              textDecoration: "none",
            }}
          >
            {link.label}
          </Link>
        )
      })}
    </div>
  )
}

export function Sidebar() {
  const pathname = usePathname() ?? ""

  return (
    <aside
      style={{
        width: 268,
        flex: "none",
        borderRight: "1px solid var(--color-divider)",
        display: "flex",
        flexDirection: "column",
        position: "sticky",
        top: 0,
        height: "100vh",
      }}
    >
      <div style={{ padding: "28px 24px", borderBottom: "1px solid var(--color-divider)" }}>
        <Link
          href="/dashboard"
          style={{
            fontFamily: "var(--font-heading)",
            fontWeight: 800,
            fontSize: 19,
            letterSpacing: "-0.01em",
            color: "inherit",
            textDecoration: "none",
          }}
        >
          FIX <span style={{ color: "var(--color-accent)" }}>+</span> APPLY
        </Link>
      </div>

      <div style={{ padding: "24px 16px", display: "flex", flexDirection: "column", gap: 28, flex: 1, overflow: "auto" }}>
        <NavGroup title="Workspace" links={workspaceLinks} pathname={pathname} />
        <NavGroup title="Start" links={startLinks} pathname={pathname} />
        <NavGroup title="Account" links={accountLinks} pathname={pathname} />
      </div>

      <TrialWidget />
    </aside>
  )
}
