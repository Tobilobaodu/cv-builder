"use client"

import Link from "next/link"
import { useRouter, usePathname } from "next/navigation"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { performLogout } from "@/lib/auth-api"
import { useAuthStore } from "@/store/auth-store"

export function Navbar() {
  const router = useRouter()
  const pathname = usePathname()
  const user = useAuthStore((state) => state.user)

  function handleLogout() {
    performLogout()
    // On a /dashboard route, useRequireAuth's own effect already redirects
    // to /login the instant accessToken goes null — pushing here too would
    // race it. Only navigate ourselves when that guard isn't in play.
    if (!pathname.startsWith("/dashboard")) {
      router.push("/")
    }
  }

  return (
    <header className="nav">
      <Link href="/" className="nav-brand" style={{ textDecoration: "none", color: "inherit" }}>
        CV TAILORING
      </Link>

      <nav style={{ display: "flex", alignItems: "center", gap: "var(--space-3)" }}>
        {user ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type="button" className="btn btn-secondary">
                {user.email}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild>
                <Link href="/dashboard">Dashboard</Link>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={handleLogout}>Log out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <>
            <Link href="/login">Log in</Link>
            <Link href="/try" className="btn btn-primary">
              Try for free
            </Link>
          </>
        )}
      </nav>
    </header>
  )
}
