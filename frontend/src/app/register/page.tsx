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
import { registerSchema, type RegisterFormValues } from "@/lib/schemas/auth"
import { registerAccount, loginAccount } from "@/lib/auth-api"
import { ApiError, errorMessage } from "@/lib/api"
import { useAuthStore } from "@/store/auth-store"
import { usePostAuthRedirect } from "@/hooks/use-post-auth-redirect"

export default function RegisterPage() {
  const redirectAfterAuth = usePostAuthRedirect()
  const setAuth = useAuthStore((state) => state.setAuth)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<RegisterFormValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: { email: "", password: "", confirmPassword: "" },
  })

  async function onSubmit(values: RegisterFormValues) {
    setIsSubmitting(true)
    try {
      await registerAccount(values.email, values.password)
      // Registration does not return a token (app/api/v1/auth.py::register
      // returns 201 UserResponse only) — log in immediately after.
      const loginResult = await loginAccount(values.email, values.password)
      setAuth(
        loginResult.accessToken,
        {
          id: loginResult.user.id,
          email: loginResult.user.email,
        },
        loginResult.refreshToken
      )
      await redirectAfterAuth()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        toast.error(errorMessage(error, "An account with this email already exists."))
      } else if (error instanceof ApiError && error.status === 429) {
        toast.error(errorMessage(error, "Too many attempts. Please wait and try again."))
      } else {
        toast.error(errorMessage(error, "Could not create your account."))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div style={{ maxWidth: 400, margin: "0 auto", padding: "96px 24px" }}>
      <h1 style={{ fontSize: 32, margin: "0 0 24px" }}>CREATE YOUR ACCOUNT</h1>
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
                  <input type="password" autoComplete="new-password" className="input" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="confirmPassword"
            render={({ field }) => (
              <FormItem className="field">
                <FormLabel>Confirm password</FormLabel>
                <FormControl>
                  <input type="password" autoComplete="new-password" className="input" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <button type="submit" className="btn btn-primary" disabled={isSubmitting} style={{ marginTop: 4 }}>
            {isSubmitting ? "Creating account…" : "Create account"}
          </button>
        </form>
      </Form>
      <p style={{ marginTop: 24, textAlign: "center", fontSize: 13, color: "var(--color-neutral-700)" }}>
        Already have an account? <Link href="/login">Log in</Link>
      </p>
    </div>
  )
}
