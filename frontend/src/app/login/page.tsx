"use client"

import { useState } from "react"
import Link from "next/link"
import { useForm } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { toast } from "sonner"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { loginSchema, type LoginFormValues } from "@/lib/schemas/auth"
import { loginAccount } from "@/lib/auth-api"
import { ApiError, errorMessage } from "@/lib/api"
import { useAuthStore } from "@/store/auth-store"
import { usePostAuthRedirect } from "@/hooks/use-post-auth-redirect"

export default function LoginPage() {
  const redirectAfterAuth = usePostAuthRedirect()
  const setAuth = useAuthStore((state) => state.setAuth)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  })

  async function onSubmit(values: LoginFormValues) {
    setIsSubmitting(true)
    try {
      const result = await loginAccount(values.email, values.password)
      setAuth(
        result.accessToken,
        {
          id: result.user.id,
          email: result.user.email,
        },
        // Stored so an expired access token can be renewed silently
        // (lib/api.ts) instead of forcing this form again.
        result.refreshToken
      )
      await redirectAfterAuth()
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        toast.error(errorMessage(error, "Too many attempts. Please wait and try again."))
      } else {
        toast.error(errorMessage(error, "Invalid email or password."))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div style={{ maxWidth: 400, margin: "0 auto", padding: "96px 24px" }}>
      <h1 style={{ fontSize: 32, margin: "0 0 24px" }}>LOG IN</h1>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          style={{ display: "flex", flexDirection: "column", gap: 20 }}
          noValidate
        >
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem className="field">
                <FormLabel>Email</FormLabel>
                <FormControl>
                  <input type="email" autoComplete="email" className="input" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem className="field">
                <FormLabel>Password</FormLabel>
                <FormControl>
                  <input type="password" autoComplete="current-password" className="input" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <button type="submit" className="btn btn-primary" disabled={isSubmitting} style={{ marginTop: 4 }}>
            {isSubmitting ? "Logging in…" : "Log in"}
          </button>
        </form>
      </Form>
      <p style={{ marginTop: 24, textAlign: "center", fontSize: 13, color: "var(--color-neutral-700)" }}>
        Don&apos;t have an account? <Link href="/register">Create one</Link>
      </p>
    </div>
  )
}
