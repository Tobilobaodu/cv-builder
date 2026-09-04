import Link from "next/link"
import { ArrowRight } from "lucide-react"

export default function LandingPage() {
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: "96px 48px" }}>
      <h1 style={{ fontSize: 42, margin: "0 0 16px", maxWidth: "18ch" }}>
        EVIDENCE-BACKED CV TAILORING AND COVER LETTERS
      </h1>
      <p style={{ margin: "0 0 32px", fontSize: 16, lineHeight: 1.6, color: "var(--color-neutral-700)", maxWidth: "56ch" }}>
        Upload your CV, paste a job link, and let our engine tailor your application in minutes.
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Link href="/try" className="btn btn-primary">
          Try it free — no credit card
          <ArrowRight width={16} height={16} strokeWidth={2.2} strokeLinecap="square" />
        </Link>
        <Link href="/login" className="btn btn-secondary">
          Log in
        </Link>
      </div>
    </div>
  )
}
