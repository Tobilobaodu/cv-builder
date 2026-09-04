"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  trialUploadSchema,
  type TrialUploadFormValues,
} from "@/lib/schemas/trial"
import { uploadCv, submitJobPostUrl, submitJobPostText } from "@/lib/trial-api"
import { errorMessage } from "@/lib/api"
import { useTrialStore } from "@/store/trial-store"

export default function TrialUploadPage() {
  const router = useRouter()
  const setWorkflow = useTrialStore((s) => s.setWorkflow)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<TrialUploadFormValues>({
    resolver: zodResolver(trialUploadSchema),
    defaultValues: { jobInputType: "text", jobUrl: "", jobText: "" },
  })

  async function onSubmit(values: TrialUploadFormValues) {
    setIsSubmitting(true)
    try {
      const cvResult = await uploadCv(values.cvFile)
      const jobResult =
        values.jobInputType === "url"
          ? await submitJobPostUrl(values.jobUrl as string)
          : await submitJobPostText(values.jobText as string)

      setWorkflow({
        cvId: cvResult.cvId,
        cvProcessingJobId: cvResult.processingJobId,
        jobPostId: jobResult.jobPostId,
        jobPostProcessingJobId: jobResult.processingJobId,
        cvProfileVersionId: null,
        matchId: null,
        draftId: null,
      })
      router.push("/try/results")
    } catch (error) {
      toast.error(errorMessage(error, "Something went wrong. Please try again."))
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-xl px-4 py-16">
      <Card>
        <CardHeader>
          <CardTitle>Upload your CV and a job posting</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex flex-col gap-6"
            noValidate
          >
            <div className="grid gap-2">
              <Label htmlFor="cv-file">CV (PDF or DOCX, up to 20MB)</Label>
              <Controller
                control={form.control}
                name="cvFile"
                render={({ field: { onChange, ref, name } }) => (
                  <Input
                    id="cv-file"
                    type="file"
                    name={name}
                    ref={ref}
                    accept=".pdf,.docx"
                    onChange={(e) => onChange(e.target.files?.[0])}
                  />
                )}
              />
              {form.formState.errors.cvFile && (
                <p className="text-sm text-destructive">
                  {form.formState.errors.cvFile.message}
                </p>
              )}
            </div>

            <Controller
              control={form.control}
              name="jobInputType"
              render={({ field }) => (
                <Tabs value={field.value} onValueChange={field.onChange}>
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="text">Paste description</TabsTrigger>
                    <TabsTrigger value="url">Job URL</TabsTrigger>
                  </TabsList>
                  <TabsContent value="text" className="mt-4">
                    <div className="grid gap-2">
                      <Label htmlFor="job-text">Job description</Label>
                      <Controller
                        control={form.control}
                        name="jobText"
                        render={({ field: textField }) => (
                          <Textarea
                            id="job-text"
                            rows={8}
                            placeholder="Paste the job posting text here…"
                            {...textField}
                          />
                        )}
                      />
                      {form.formState.errors.jobText && (
                        <p className="text-sm text-destructive">
                          {form.formState.errors.jobText.message}
                        </p>
                      )}
                    </div>
                  </TabsContent>
                  <TabsContent value="url" className="mt-4">
                    <div className="grid gap-2">
                      <Label htmlFor="job-url">Job posting URL</Label>
                      <Controller
                        control={form.control}
                        name="jobUrl"
                        render={({ field: urlField }) => (
                          <Input
                            id="job-url"
                            type="url"
                            placeholder="https://example.com/careers/role"
                            {...urlField}
                          />
                        )}
                      />
                      {form.formState.errors.jobUrl && (
                        <p className="text-sm text-destructive">
                          {form.formState.errors.jobUrl.message}
                        </p>
                      )}
                    </div>
                  </TabsContent>
                </Tabs>
              )}
            />

            <Button type="submit" disabled={isSubmitting} size="lg">
              {isSubmitting ? "Uploading…" : "Run my match"}
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              No credit card required — one free tailored CV and score.
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
