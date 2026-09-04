import { redirect } from "next/navigation"

/** The tailor flow now lives at /try/upload — it replaced the old
 *  upload-then-poll trial page rather than sitting beside it. This
 *  redirect keeps any link handed out while it was at /try/tailor alive. */
export default function TailorRedirect() {
  redirect("/try/upload")
}
