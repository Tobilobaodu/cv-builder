import { describe, expect, it } from "vitest"
import { trialUploadSchema } from "@/lib/schemas/trial"

function makeFile(name: string, sizeBytes: number, type = "application/pdf") {
  const file = new File([new Uint8Array(sizeBytes)], name, { type })
  return file
}

describe("trialUploadSchema", () => {
  it("accepts a valid PDF under the size limit with pasted text", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile("resume.pdf", 1024),
      jobInputType: "text",
      jobText: "x".repeat(150),
    })
    expect(result.success).toBe(true)
  })

  it("accepts a valid DOCX with a job URL", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile(
        "resume.docx",
        1024,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      ),
      jobInputType: "url",
      jobUrl: "https://example.com/careers/role",
    })
    expect(result.success).toBe(true)
  })

  it("rejects an unsupported file extension", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile("resume.txt", 1024, "text/plain"),
      jobInputType: "text",
      jobText: "x".repeat(150),
    })
    expect(result.success).toBe(false)
  })

  it("rejects a file over 20MB", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile("resume.pdf", 21 * 1024 * 1024),
      jobInputType: "text",
      jobText: "x".repeat(150),
    })
    expect(result.success).toBe(false)
  })

  it("rejects job text under 100 characters", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile("resume.pdf", 1024),
      jobInputType: "text",
      jobText: "too short",
    })
    expect(result.success).toBe(false)
  })

  it("rejects an invalid job URL", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile("resume.pdf", 1024),
      jobInputType: "url",
      jobUrl: "not-a-url",
    })
    expect(result.success).toBe(false)
  })

  it("rejects a missing job URL when jobInputType is url", () => {
    const result = trialUploadSchema.safeParse({
      cvFile: makeFile("resume.pdf", 1024),
      jobInputType: "url",
      jobUrl: "",
    })
    expect(result.success).toBe(false)
  })
})
