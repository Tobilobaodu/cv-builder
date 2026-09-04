import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import express, { Router, type IRouter } from "express";

const execFileAsync = promisify(execFile);
const router: IRouter = Router();

function xmlToText(xml: string) {
  return xml
    .replace(/<w:tab\/>/g, "\t")
    .replace(/<\/w:p>/g, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

router.post(
  "/resume/extract",
  express.raw({
    type: ["application/octet-stream", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/plain", "text/markdown"],
    limit: "10mb",
  }),
  async (req, res) => {
    const fileName = String(req.header("x-resume-filename") || "uploaded-resume.txt");
    const extension = path.extname(fileName).toLowerCase();
    const fileBuffer = Buffer.isBuffer(req.body) ? req.body : Buffer.from([]);

    if (!fileBuffer.length) {
      res.status(400).json({ error: "The uploaded file was empty." });
      return;
    }

    if ([".txt", ".md", ".text"].includes(extension)) {
      res.json({ resumeText: fileBuffer.toString("utf8").trim(), originalFileName: fileName });
      return;
    }

    const tempPath = path.join(os.tmpdir(), `resume-tailor-${randomUUID()}${extension}`);
    try {
      await fs.writeFile(tempPath, fileBuffer);
      let resumeText = "";

      if (extension === ".pdf") {
        const result = await execFileAsync("pdftotext", ["-layout", tempPath, "-"], { maxBuffer: 500_000 });
        resumeText = result.stdout.trim();
      } else if (extension === ".docx") {
        const result = await execFileAsync("unzip", ["-p", tempPath, "word/document.xml"], { maxBuffer: 500_000 });
        resumeText = xmlToText(result.stdout);
      } else {
        res.status(400).json({ error: "Upload a PDF, DOCX, TXT, or Markdown file." });
        return;
      }

      if (resumeText.length < 20) {
        res.status(400).json({ error: "We couldn't find readable text in that file. Try exporting it again or paste the resume text below." });
        return;
      }

      res.json({ resumeText, originalFileName: fileName });
    } catch (error) {
      req.log.error({ err: error, fileName }, "Resume extraction failed");
      res.status(400).json({ error: "We couldn't read that file. Try a PDF, DOCX, TXT, or Markdown export, or paste the text below." });
    } finally {
      await fs.rm(tempPath, { force: true });
    }
  },
);

export default router;