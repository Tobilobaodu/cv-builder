import { LogOut } from "lucide-react"

import { performLogout } from "@/lib/auth-api"
import { useAuthStore } from "@/store/auth-store"

function initialsFromEmail(email: string | undefined): string {
  if (!email) return "?"
  const local = email.split("@")[0] ?? email
  const parts = local.split(/[._-]+/).filter(Boolean)
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase()
  }
  return local.slice(0, 2).toUpperCase()
}

export function Topbar({ crumb }: { crumb: string }) {
  const email = useAuthStore((state) => state.user?.email)

  return (
    <div
      style={{
        height: 87,
        borderBottom: "1px solid var(--color-divider)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "0 48px",
        gap: 24,
      }}
    >
      <div style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>{crumb}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
        <div style={{ fontSize: 13 }}>{email ?? ""}</div>
        <div
          style={{
            width: 32,
            height: 32,
            background: "var(--color-text)",
            color: "var(--color-bg)",
            display: "grid",
            placeItems: "center",
            fontFamily: "var(--font-heading)",
            fontWeight: 800,
            fontSize: 12,
          }}
        >
          {initialsFromEmail(email)}
        </div>
        <button
          type="button"
          className="btn btn-ghost"
          aria-label="Log out"
          onClick={performLogout}
        >
          <LogOut width={16} height={16} strokeWidth={2} />
        </button>
      </div>
    </div>
  )
}
