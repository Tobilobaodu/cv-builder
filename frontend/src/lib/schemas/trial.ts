import { z } from "zod"

// Mirrors backend app/services/file_validation.py: ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB.
const ALLOWED_EXTENSIONS = [".pdf", ".docx"]
const MAX_FILE_SIZE_MB = 20
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

const cvFileSchema = z
  .instanceof(File, { message: "Choose a CV file." })
  .refine(
    (file) => ALLOWED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext)),
    "Only PDF and DOCX files are supported."
  )
  .refine(
    (file) => file.size > 0 && file.size <= MAX_FILE_SIZE_BYTES,
    `File must be under ${MAX_FILE_SIZE_MB}MB.`
  )

export const trialUploadSchema = z
  .object({
    cvFile: cvFileSchema,
    jobInputType: z.enum(["url", "text"]),
    jobUrl: z.string().optional(),
    jobText: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.jobInputType === "url") {
      const result = z.string().url("Enter a valid job posting URL.").safeParse(data.jobUrl)
      if (!result.success) {
        ctx.addIssue({
          code: "custom",
          path: ["jobUrl"],
          message: result.error.issues[0]?.message ?? "Enter a valid job posting URL.",
        })
      }
    } else {
      // Mirrors backend app/api/v1/job_posts.py: JobPostTextRequest min_length=100.
      if (!data.jobText || data.jobText.length < 100) {
        ctx.addIssue({
          code: "custom",
          path: ["jobText"],
          message: "Paste at least 100 characters of the job description.",
        })
      }
    }
  })

export type TrialUploadFormValues = z.infer<typeof trialUploadSchema>
