"use client"

import { useState } from "react"

/** A link next to the CV upload control offering LinkedIn as an alternative
 *  source. Deliberately just an instructional disclosure, not a live
 *  integration: LinkedIn's self-serve sign-in API never exposes work
 *  history (name/email/photo only), and scraping a profile is against
 *  their Terms of Service — the one clean, ToS-compliant path is the
 *  user's own "Save to PDF" export, which is just a PDF and needs no
 *  special handling once uploaded through the normal CV input below. */
export function LinkedInImportHint({ onOpen }: { onOpen?: () => void }) {
  const [open, setOpen] = useState(false)

  function toggle() {
    setOpen((wasOpen) => {
      if (!wasOpen) onOpen?.()
      return !wasOpen
    })
  }

  return (
    <div style={{ marginTop: 8 }}>
      <button
        type="button"
        data-testid="button-linkedin-import"
        onClick={toggle}
        style={{
          background: "none",
          border: 0,
          padding: 0,
          cursor: "pointer",
          font: "inherit",
          fontSize: 13,
          color: "var(--color-accent)",
          textDecoration: "underline",
          textUnderlineOffset: 3,
        }}
      >
        {open ? "Hide LinkedIn import steps" : "No CV file handy? Import from LinkedIn instead"}
      </button>

      {open && (
        <div
          data-testid="panel-linkedin-import"
          style={{
            marginTop: 10,
            padding: 12,
            border: "1px solid var(--color-divider)",
            background: "var(--color-bg)",
            fontSize: 13,
            color: "var(--color-neutral-700)",
          }}
        >
          <ol style={{ margin: "0 0 8px", paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
            <li>Go to your LinkedIn profile.</li>
            <li>Click the &quot;More&quot; button below your profile photo.</li>
            <li>Select &quot;Save to PDF.&quot;</li>
            <li>Upload the downloaded file below — it works exactly like a CV.</li>
          </ol>
          <a href="https://www.linkedin.com" target="_blank" rel="noopener noreferrer">
            Open LinkedIn ↗
          </a>
        </div>
      )}
    </div>
  )
}
