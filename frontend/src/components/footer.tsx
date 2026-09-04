export function Footer() {
  return (
    <footer style={{ borderTop: "1px solid var(--color-divider)" }}>
      <div
        style={{
          maxWidth: 1180,
          margin: "0 auto",
          padding: "var(--space-4) var(--space-4)",
          fontSize: 13,
          color: "var(--color-neutral-700)",
        }}
      >
        © {new Date().getFullYear()} CV Tailoring. All rights reserved.
      </div>
    </footer>
  )
}
