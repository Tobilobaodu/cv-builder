import { z } from "zod"

// Mirrors backend app/schemas/auth.py: RegisterRequest.password min_length=12.
// This MUST match the backend. It previously said 8 while the backend
// required 12, so a 8-11 character password passed client validation and
// then failed server-side with a 422 the register page did not surface —
// the account was silently never created.
export const registerSchema = z
  .object({
    email: z.string().email("Enter a valid email address."),
    password: z.string().min(12, "Password must be at least 12 characters."),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  })

export type RegisterFormValues = z.infer<typeof registerSchema>

export const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Password is required."),
})

export type LoginFormValues = z.infer<typeof loginSchema>
