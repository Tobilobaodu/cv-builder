"use client"

import { toast } from "sonner"
import { useAuthStore } from "@/store/auth-store"

/**
 * Account form fields beyond email are inert/unsaved by design — there is
 * no backend field for name/career level/target industry yet (see task
 * spec). Plan comparison and invoices are static content verbatim from the
 * mockup; there is no billing backend, so "Upgrade now" is an inert action
 * with a "coming soon" toast rather than a real checkout flow.
 */
export default function SettingsPage() {
  const email = useAuthStore((state) => state.user?.email) ?? ""

  return (
    <div style={{ padding: 48, display: "flex", flexDirection: "column", gap: 40, maxWidth: 1000 }}>
      <div>
        <h1 style={{ fontSize: 42, margin: "0 0 8px" }}>SETTINGS &amp; BILLING</h1>
        <p style={{ margin: 0, fontSize: 15, color: "var(--color-neutral-700)" }}>Account, plan and invoices.</p>
      </div>

      <section>
        <h3 style={{ fontSize: 20, margin: "0 0 20px" }}>ACCOUNT</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px 32px", maxWidth: 680 }}>
          <div className="field">
            <label>Full name</label>
            <input className="input" placeholder="Add your name" />
          </div>
          <div className="field">
            <label>Email</label>
            <input className="input" value={email} readOnly />
          </div>
          <div className="field">
            <label>Career level</label>
            <select className="input" defaultValue="">
              <option value="" disabled>
                Not set
              </option>
              <option>Junior</option>
              <option>Mid</option>
              <option>Senior</option>
              <option>Lead / Staff</option>
            </select>
          </div>
          <div className="field">
            <label>Target industry</label>
            <select className="input" defaultValue="">
              <option value="" disabled>
                Not set
              </option>
              <option>Product design</option>
              <option>Engineering</option>
            </select>
          </div>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          style={{ marginTop: 24 }}
          onClick={() => toast.info("Profile fields aren't saved yet — coming soon.")}
        >
          Save changes
        </button>
      </section>

      <section style={{ borderTop: "1px solid var(--color-divider)", paddingTop: 32 }}>
        <h3 style={{ fontSize: 20, margin: "0 0 20px" }}>PLAN</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2, background: "var(--color-divider)" }}>
          <div style={{ background: "var(--color-surface)", padding: 28 }}>
            <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-700)", marginBottom: 10 }}>
              Current
            </div>
            <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 25, marginBottom: 16 }}>Free trial</div>
            <div style={{ display: "flex", height: 10, gap: 2, marginBottom: 10 }}>
              <div
                style={{
                  width: "33%",
                  background:
                    "repeating-linear-gradient(90deg, var(--color-accent) 0 3px, transparent 3px 5px)",
                }}
              />
              <div
                style={{
                  flex: 1,
                  background:
                    "repeating-linear-gradient(90deg, var(--color-neutral-400) 0 3px, transparent 3px 5px)",
                }}
              />
            </div>
            <div style={{ fontSize: 13, color: "var(--color-neutral-700)" }}>1 of 3 rewrites used · resets never</div>
          </div>
          <div style={{ background: "var(--color-accent)", color: "var(--color-bg)", padding: 28 }}>
            <div style={{ fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", opacity: 0.85, marginBottom: 10 }}>
              Recommended
            </div>
            <div style={{ fontFamily: "var(--font-heading)", fontWeight: 800, fontSize: 25, marginBottom: 8 }}>
              Unlimited — £20/mo
            </div>
            <div style={{ fontSize: 14, lineHeight: 1.55, marginBottom: 20, maxWidth: "34ch" }}>
              Unlimited rewrites, cover letters, coverage reports and .docx exports. Cancel any time.
            </div>
            <button
              type="button"
              className="btn"
              style={{ background: "var(--color-bg)", color: "var(--color-text)" }}
              onClick={() => toast.info("Billing isn't available yet — coming soon.")}
            >
              Upgrade now
            </button>
          </div>
        </div>
      </section>

      <section style={{ borderTop: "1px solid var(--color-divider)", paddingTop: 32 }}>
        <h3 style={{ fontSize: 20, margin: "0 0 20px" }}>INVOICES</h3>
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Description</th>
              <th>Amount</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={{ color: "var(--color-neutral-700)" }}>—</td>
              <td>Free trial — no charge</td>
              <td>£0.00</td>
              <td style={{ textAlign: "right" }}>
                <span className="btn btn-ghost" style={{ opacity: 0.5, cursor: "default" }}>
                  Receipt
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  )
}
