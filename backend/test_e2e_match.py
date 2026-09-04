"""End-to-end match test: register → upload CV → parse → job post → match → evidence.

Pipe into the API container:
  docker compose exec -T api python < test_e2e_match.py
  (on Windows: Get-Content test_e2e_match.py | docker compose exec -T api python)
"""

import io
import json
import time
import sys
import urllib.request
import urllib.error
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.encoders import encode_base64

API = "http://localhost:8000/api/v1"
EMAIL = f"e2e-match-{int(time.time())}@test.com"
PASSWORD = "E2ETest2024!"


# ── HTTP helpers ──────────────────────────────────────────────────────
def _req(method: str, path: str, headers: dict = None, body: bytes = None,
         json_body: dict = None, files: dict = None, expect_status: int = None) -> tuple[int, dict]:
    """Send an HTTP request, return (status_code, json_decoded_body_or_error_dict)."""
    url = f"{API}{path}"
    hdrs = headers.copy() if headers else {}
    data = None

    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        if "Content-Type" not in hdrs:
            hdrs["Content-Type"] = "application/json"

    elif files is not None:
        boundary = uuid.uuid4().hex
        hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        parts = []
        for field_name, (filename, filedata, mime) in files.items():
            part = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode("utf-8")
            part += filedata if isinstance(filedata, bytes) else filedata.encode("utf-8")
            part += b"\r\n"
            parts.append(part)
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        data = b"".join(parts)

    elif body is not None:
        data = body

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_body = resp.read()
            try:
                return resp.status, json.loads(resp_body)
            except json.JSONDecodeError:
                return resp.status, {"_raw": resp_body.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        err_body = e.read()
        try:
            return e.code, json.loads(err_body)
        except json.JSONDecodeError:
            return e.code, {"_raw": err_body.decode("utf-8", errors="replace")}
    except Exception as e:
        return 0, {"_error": str(e)}


# ── Generate a text-content PDF (no external libs) ────────────────────
def make_cv_pdf() -> bytes:
    """Build a minimal PDF containing extractable text for a CV."""
    text = (
        "John Doe\n"
        "Software Engineer\n"
        "Email: john.doe@example.com | Phone: +1-555-0123\n\n"
        "Summary\n"
        "Experienced software engineer with 8 years building cloud-native systems.\n"
        "Proficient in Python, Docker, Kubernetes, and AWS. Strong background in\n"
        "distributed systems and CI/CD pipelines.\n\n"
        "Experience\n"
        "Senior Engineer - Acme Corp (2020-Present)\n"
        "- Built microservices platform serving 10M+ requests/day on AWS EKS\n"
        "- Led adoption of Docker and Kubernetes across 12 engineering teams\n"
        "- Developed CI/CD pipelines reducing deploy time from hours to minutes\n"
        "- Implemented monitoring with Prometheus and Grafana\n\n"
        "Software Engineer - Beta Inc (2017-2020)\n"
        "- Designed REST APIs with Python FastAPI handling 1M+ daily requests\n"
        "- Managed PostgreSQL databases with replication and failover\n"
        "- Wrote Terraform modules for multi-region AWS infrastructure\n\n"
        "Education\n"
        "BSc Computer Science - University of Technology (2013-2017)\n\n"
        "Skills\n"
        "Python, Docker, Kubernetes, AWS, Terraform, FastAPI, PostgreSQL,\n"
        "CI/CD, Git, Linux, Prometheus, Grafana, REST APIs, Microservices,\n"
        "Distributed Systems, GitLab CI, GitHub Actions\n\n"
        "Certifications\n"
        "AWS Solutions Architect Associate, CKAD: Certified Kubernetes Application Developer"
    )
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    ).encode("latin-1")
    text_obj = (
        b"BT\n"
        b"/F1 12 Tf\n"
        b"50 720 Td\n"
        b"15 TL\n"
        b"(" + escaped + b") Tj\n"
        b"ET"
    )

    objects = []
    offsets: list[int] = []

    # Object 1: Catalog
    offsets.append(len(b"".join(objects)))
    objects.append(b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")

    # Object 2: Pages
    offsets.append(len(b"".join(objects)))
    objects.append(b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n")

    # Object 3: Page
    offsets.append(len(b"".join(objects)))
    objects.append(
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
    )

    # Object 4: Font (Helvetica, standard)
    offsets.append(len(b"".join(objects)))
    objects.append(b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n")

    # Object 5: Content stream with text
    offsets.append(len(b"".join(objects)))
    stream = text_obj
    objects.append(
        b"5 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n"
        + stream + b"\nendstream\nendobj\n"
    )

    xref_offset = len(b"".join(objects))
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        b"trailer<</Size 6/Root 1 0 R>>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    )

    return b"%PDF-1.4\n" + b"".join(objects) + xref + trailer


# ── Main test ─────────────────────────────────────────────────────────
def main():
    headers: dict = {}
    passed = 0
    failed = 0

    def check(step: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS {step}")
        else:
            failed += 1
            print(f"  FAIL {step}: {detail}")
            if failed >= 3:
                print("\nToo many failures, aborting.")
                sys.exit(1)

    # ── 1. Register ───────────────────────────────────────────────────
    print("1. Registering new user...")
    code, body = _req("POST", "/auth/register", json_body={
        "email": EMAIL, "password": PASSWORD
    })
    check("Register accepted (201 or 200)", code in (200, 201), str(body)[:200])
    if code == 409:
        print("   (email already exists - continuing)")

    # ── 2. Login ──────────────────────────────────────────────────────
    print("2. Logging in...")
    code, body = _req("POST", "/auth/login", json_body={
        "email": EMAIL, "password": PASSWORD
    })
    check("Login success (200)", code == 200, str(body)[:200])
    token = body.get("access_token", "")
    check("Access token present", bool(token))
    headers = {"Authorization": f"Bearer {token}"}

    # ── 3. Upload CV ──────────────────────────────────────────────────
    print("3. Uploading CV (text-content PDF)...")
    pdf = make_cv_pdf()
    print(f"   PDF size: {len(pdf)} bytes")
    code, body = _req("POST", "/cvs", headers=headers, files={
        "file": ("cv.pdf", pdf, "application/pdf")
    })
    check("Upload accepted (202)", code == 202, str(body)[:200])
    cv_id = body.get("cvId") or body.get("cv_id")
    proc_job_id = body.get("processingJobId") or body.get("processing_job_id")
    check("cvId present", bool(cv_id))
    check("processingJobId present", bool(proc_job_id))
    print(f"   cvId={cv_id}, jobId={proc_job_id}")

    # ── 4. Poll until CV pipeline completes ───────────────────────────
    print("4. Waiting for CV pipeline (docling->textract->merge->cv_parse)...")
    timeout = 300
    start_t = time.monotonic()
    cv_done = False
    last_status = ""
    while time.monotonic() - start_t < timeout:
        code, body = _req("GET", f"/jobs/{proc_job_id}", headers=headers)
        if code != 200:
            time.sleep(2)
            continue
        status = body.get("status", "unknown")
        if status != last_status:
            print(f"   ... {status}")
            last_status = status
        if status == "failed":
            check("CV pipeline", False, f"Job failed: {body.get('lastError', body.get('last_error'))}")
            return
        if status == "completed":
            code2, body2 = _req("GET", f"/cvs/{cv_id}", headers=headers)
            if code2 == 200:
                cv_status = body2.get("processing_status", "")
                print(f"   CV processing_status: {cv_status}")
                if cv_status in ("parsed", "completed"):
                    cv_done = True
                    break
        time.sleep(3)
    check("CV pipeline completed in time", cv_done, "Timed out")
    if not cv_done:
        return

    # ── 5. Get parsed profile ─────────────────────────────────────────
    print("5. Fetching parsed profile...")
    code, body = _req("GET", f"/cvs/{cv_id}/parsed-profile", headers=headers)
    check("Parsed profile returned (200)", code == 200, str(body)[:200])
    cv_profile_version_id = body.get("profileVersionId")
    check("profileVersionId present", bool(cv_profile_version_id))
    skills_data = body.get("structuredPayload", {}).get("skills", {})
    print(f"   Extracted skills: {json.dumps(skills_data, indent=2)[:500]}")
    print(f"   profileVersionId: {cv_profile_version_id}")

    # ── 6. Submit job post ────────────────────────────────────────────
    print("6. Submitting job post (Senior Platform Engineer)...")
    job_text = (
        "Senior Platform Engineer\n\n"
        "About Us: We build infrastructure for global financial services.\n\n"
        "Requirements:\n"
        "- 5+ years experience with Python\n"
        "- Expert knowledge of Docker and Kubernetes\n"
        "- AWS architecture and operations (ECS, EKS, RDS)\n"
        "- Terraform infrastructure-as-code\n"
        "- CI/CD pipeline design (GitHub Actions or GitLab CI)\n"
        "- PostgreSQL administration\n\n"
        "Preferred:\n"
        "- Machine Learning operations (MLOps)\n"
        "- Service mesh (Istio/Linkerd)\n"
        "- Kafka or similar event streaming\n"
        "- Go or Rust experience\n\n"
        "Responsibilities:\n"
        "- Design and maintain cloud infrastructure\n"
        "- Build internal developer platform\n"
        "- Mentor junior engineers\n"
    )
    code, body = _req("POST", "/job-posts/text", headers=headers, json_body={
        "text": job_text
    })
    check("Job post accepted (202)", code == 202, str(body)[:200])
    jp_id = body.get("jobPostId")
    jp_job_id = body.get("processingJobId")
    check("jobPostId present", bool(jp_id))
    print(f"   jobPostId={jp_id}, jobId={jp_job_id}")

    # ── 7. Wait for job post parse ────────────────────────────────────
    print("7. Waiting for job post parsing...")
    start_t = time.monotonic()
    jp_done = False
    while time.monotonic() - start_t < 60:
        code, body = _req("GET", f"/jobs/{jp_job_id}", headers=headers)
        if code != 200:
            time.sleep(1)
            continue
        status = body.get("status", "unknown")
        if status == "completed":
            jp_done = True
            break
        if status == "failed":
            check("Job post parse", False, str(body.get("lastError", body.get("last_error"))))
            return
        time.sleep(1)
    check("Job post parsed in time", jp_done, "Timed out")

    # ── 8. Inspect job post profile ───────────────────────────────────
    print("8. Inspecting job post profile...")
    code, body = _req("GET", f"/job-posts/{jp_id}", headers=headers)
    check("Job post GET (200)", code == 200, str(body)[:200])
    jp_profile = body.get("profile", {})
    print(f"   Title: {jp_profile.get('jobTitle')}")
    print(f"   Required: {jp_profile.get('requiredSkills')}")
    print(f"   Preferred: {jp_profile.get('preferredSkills')}")

    # ── 9. Create match ───────────────────────────────────────────────
    print("9. Creating match analysis...")
    code, body = _req("POST", "/matches", headers=headers, json_body={
        "cvProfileVersionId": cv_profile_version_id,
        "jobPostId": jp_id,
    })
    check("Match created (202)", code == 202, str(body)[:200])
    match_id = body.get("matchId")
    match_job_id = body.get("processingJobId")
    check("matchId present", bool(match_id))
    print(f"   matchId={match_id}, jobId={match_job_id}")

    # ── 10. Poll for match completion ─────────────────────────────────
    print("10. Waiting for match engine...")
    start_t = time.monotonic()
    match_done = False
    while time.monotonic() - start_t < 30:
        code, body = _req("GET", f"/matches/{match_id}", headers=headers)
        if code != 200:
            time.sleep(1)
            continue
        status = body.get("status", "unknown")
        print(f"   ... {status}")
        if status in ("completed", "failed"):
            match_done = True
            break
        time.sleep(1)
    check("Match completed in time", match_done, "Timed out")

    # ── 11. Display evidence ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("MATCH RESULT")
    print("=" * 70)
    code, body = _req("GET", f"/matches/{match_id}", headers=headers)

    print(f"Status:     {body.get('status')}")
    print(f"Score:      {body.get('score')}")
    print(f"Supported:  {body.get('supportedCount', body.get('supported_count'))}")
    print(f"Partial:    {body.get('partialCount', body.get('partial_count'))}")
    print(f"Unsupported:{body.get('unsupportedCount', body.get('unsupported_count'))}")
    print(f"Total reqs: {body.get('totalRequirements', body.get('total_requirements'))}")
    summary = body.get("summaryAnalysis") or body.get("summary_analysis")
    if summary:
        print(f"\nSummary:\n{summary}")

    evidence = body.get("evidenceItems") or body.get("evidence_items") or []
    if evidence:
        print(f"\n{len(evidence)} evidence items:")
        print("-" * 70)
        icons = {
            "supported": "GREEN",
            "partially_supported": "YELLOW",
            "unsupported": "RED",
            "contradictory": "ORANGE",
            "unclear": "GREY",
        }
        for i, ei in enumerate(evidence):
            req_text = ei.get("requirementText") or ei.get("requirement_text", "?")
            level = ei.get("supportLevel") or ei.get("support_level", "?")
            conf = ei.get("confidence")
            req_type = ei.get("requirementType") or ei.get("requirement_type", "?")
            icon = icons.get(level, "?")
            print(f"  [{icon}] [{level}] ({req_type}) {req_text}")
            if conf is not None:
                print(f"       confidence={conf:.2f}")
            suggestion = ei.get("suggestion")
            if suggestion:
                print(f"       SUGGESTION: {suggestion}")
            warning = ei.get("warning")
            if warning:
                print(f"       WARNING: {warning}")
        print("-" * 70)

    error_msg = body.get("errorMessage") or body.get("error_message")
    if error_msg:
        print(f"\nError: {error_msg}")

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        print("\nSOME CHECKS FAILED")
        sys.exit(1)
    else:
        print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()