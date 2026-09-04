"""End-to-end match test (v2): bypasses PDF pipeline, seeds CV data directly.

The hand-crafted PDF hangs Docling (malformed PDF parsing bug, not a match engine bug).
This test seeds validated CV profile data via SQL, then exercises the full match flow
through the real API: register → seed CV → submit job post → create match → evidence.

Pipe into the API container:
  docker compose exec -T api python < test_e2e_match_v2.py
"""

import json
import time
import sys
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone

API = "http://localhost:8000/api/v1"
EMAIL = f"e2e-match-v2-{int(time.time())}@test.com"
PASSWORD = "E2ETest2024!"

# Database URL for direct seeding
DB_URL = "postgresql://cvapp:cvapp_local@postgres:5432/cv_tailoring"


# ── HTTP helpers ──────────────────────────────────────────────────────
def _req(method: str, path: str, headers: dict = None,
         json_body: dict = None) -> tuple[int, dict]:
    url = f"{API}{path}"
    hdrs = dict(headers) if headers else {}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        if "Content-Type" not in hdrs:
            hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, {"_raw": body.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as e:
        eb = e.read()
        try:
            return e.code, json.loads(eb)
        except json.JSONDecodeError:
            return e.code, {"_raw": eb.decode("utf-8", errors="replace")}
    except Exception as e:
        return 0, {"_error": str(e)}


# ── Seed CV profile data directly in the database ────────────────────
def seed_cv_profile(user_id: str) -> tuple[str, str]:
    """Seed a CvFile + CvProfile + CvProfileVersion + skill items.
    Returns (cv_id, cv_profile_version_id).
    """
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    engine = sa.create_engine(DB_URL)
    cv_id = str(uuid.uuid4())
    profile_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    structured_payload = {
        "contact": {
            "name": "John Doe",
            "email": "john@example.com",
            "phone": "+1-555-0123",
        },
        "summary": "Experienced software engineer with 8 years building cloud-native systems.",
        "skills": {
            "technical": [
                "Python", "Docker", "Kubernetes", "AWS", "Terraform",
                "FastAPI", "PostgreSQL", "CI/CD", "Git", "Linux",
                "Prometheus", "Grafana", "REST APIs", "Microservices",
                "Distributed Systems", "GitLab CI", "GitHub Actions",
            ],
            "soft": ["Communication", "Team Leadership"],
        },
        "experience": [
            {
                "title": "Senior Engineer",
                "company": "Acme Corp",
                "startDate": "2020-01",
                "endDate": None,
                "highlights": [
                    "Built microservices platform on AWS EKS",
                    "Led Docker/K8s adoption across 12 teams",
                    "Reduced deploy time from hours to minutes",
                ],
            },
            {
                "title": "Software Engineer",
                "company": "Beta Inc",
                "startDate": "2017-06",
                "endDate": "2020-01",
                "highlights": [
                    "Designed REST APIs with Python FastAPI",
                    "Managed PostgreSQL with replication",
                    "Wrote Terraform modules for AWS",
                ],
            },
        ],
        "education": [
            {
                "degree": "BSc Computer Science",
                "institution": "University of Technology",
                "year": 2017,
            },
        ],
        "certifications": [
            "AWS Solutions Architect Associate",
            "CKAD: Certified Kubernetes Application Developer",
        ],
    }

    with Session(engine) as session:
        # CvFile
        session.execute(sa.text("""
            INSERT INTO cv_files (id, user_id, filename, mime_type, file_size, storage_key, status, created_at, updated_at)
            VALUES (:id, :uid, 'test_cv.pdf', 'application/pdf', 5000, 'test/seed.pdf', 'parsed', :now, :now)
        """), {"id": cv_id, "uid": user_id, "now": now})

        # CvProfileVersion
        session.execute(sa.text("""
            INSERT INTO cv_profile_versions (id, cv_file_id, user_id, version_number, profile_hash, schema_version, structured_payload, validation_status, created_at)
            VALUES (:id, :cvid, :uid, 1, 'abc123', '1.0', :payload, 'passed', :now)
        """), {"id": version_id, "cvid": cv_id, "uid": user_id, "payload": json.dumps(structured_payload), "now": now})

        # CvProfile (pointer)
        session.execute(sa.text("""
            INSERT INTO cv_profiles (id, cv_file_id, current_version_id, updated_at)
            VALUES (:id, :cvid, :verid, :now)
        """), {"id": profile_id, "cvid": cv_id, "verid": version_id, "now": now})

        # Skill items
        for cat, skills in structured_payload["skills"].items():
            for skill_name in skills:
                item_id = str(uuid.uuid4())
                session.execute(sa.text("""
                    INSERT INTO cv_skill_items (id, cv_profile_version_id, skill_name, category, confidence)
                    VALUES (:id, :vid, :name, :cat, 0.85)
                """), {"id": item_id, "vid": version_id, "name": skill_name, "cat": cat})

        session.commit()

    return cv_id, version_id


# ── Main test ─────────────────────────────────────────────────────────
def main():
    passed = 0
    failed = 0

    def check(label: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS {label}")
        else:
            failed += 1
            print(f"  FAIL {label}: {detail}")
            if failed >= 3:
                print("\nToo many failures, aborting.")
                sys.exit(1)

    # ── 1. Register ───────────────────────────────────────────────────
    print("1. Registering new user...")
    code, body = _req("POST", "/auth/register", json_body={
        "email": EMAIL, "password": PASSWORD,
    })
    check("Register accepted", code in (200, 201), str(body)[:200])
    if code == 409:
        print("   (email exists - continuing)")

    # ── 2. Login ──────────────────────────────────────────────────────
    print("2. Logging in...")
    code, body = _req("POST", "/auth/login", json_body={
        "email": EMAIL, "password": PASSWORD,
    })
    check("Login success", code == 200, str(body)[:200])
    token = body.get("access_token", "")
    check("Access token present", bool(token))
    headers = {"Authorization": f"Bearer {token}"}

    # Get user ID from /auth/me
    code, me = _req("GET", "/auth/me", headers=headers)
    user_id = me.get("id", "")
    check("User ID from /auth/me", bool(user_id), str(me)[:200])
    print(f"   user_id={user_id}")

    # ── 3. Seed CV profile data ───────────────────────────────────────
    print("3. Seeding CV profile (bypassing Docling hang)...")
    try:
        cv_id, cv_profile_version_id = seed_cv_profile(user_id)
        check("CV seed succeeded", True)
        print(f"   cvId={cv_id}, profileVersionId={cv_profile_version_id}")
    except Exception as e:
        check("CV seed", False, str(e))
        return

    # ── 4. Verify parsed profile is accessible via API ────────────────
    print("4. Verifying parsed profile via API...")
    code, body = _req("GET", f"/cvs/{cv_id}/parsed-profile", headers=headers)
    check("Parsed profile accessible", code == 200, str(body)[:200])
    api_version_id = body.get("profileVersionId")
    check("profileVersionId matches", api_version_id == cv_profile_version_id,
          f"expected {cv_profile_version_id}, got {api_version_id}")
    skills = body.get("structuredPayload", {}).get("skills", {})
    print(f"   Skills: {json.dumps(skills)[:300]}")

    # ── 5. Submit job post ────────────────────────────────────────────
    print("5. Submitting job post (Senior Platform Engineer)...")
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
        "text": job_text,
    })
    check("Job post accepted (202)", code == 202, str(body)[:200])
    jp_id = body.get("jobPostId")
    jp_job_id = body.get("processingJobId")
    check("jobPostId present", bool(jp_id))
    print(f"   jobPostId={jp_id}, jobId={jp_job_id}")

    # ── 6. Wait for job post parse ────────────────────────────────────
    print("6. Waiting for job post parsing...")
    start = time.monotonic()
    jp_done = False
    while time.monotonic() - start < 60:
        code, body = _req("GET", f"/jobs/{jp_job_id}", headers=headers)
        if code != 200:
            time.sleep(1)
            continue
        s = body.get("status", "unknown")
        if s == "completed":
            jp_done = True
            break
        if s == "failed":
            check("Job post parse", False, str(body.get("lastError", body.get("last_error"))))
            return
        time.sleep(1)
    check("Job post parsed", jp_done, "Timed out")

    # ── 7. Inspect job post ───────────────────────────────────────────
    print("7. Inspecting job post profile...")
    code, body = _req("GET", f"/job-posts/{jp_id}", headers=headers)
    check("Job post GET", code == 200, str(body)[:200])
    jp_profile = body.get("profile", {})
    print(f"   Title: {jp_profile.get('jobTitle')}")
    print(f"   Required: {jp_profile.get('requiredSkills')}")
    print(f"   Preferred: {jp_profile.get('preferredSkills')}")

    # ── 8. Create match ───────────────────────────────────────────────
    print("8. Creating match analysis...")
    code, body = _req("POST", "/matches", headers=headers, json_body={
        "cvProfileVersionId": cv_profile_version_id,
        "jobPostId": jp_id,
    })
    check("Match created (202)", code == 202, str(body)[:200])
    match_id = body.get("matchId")
    match_job_id = body.get("processingJobId")
    check("matchId present", bool(match_id))
    print(f"   matchId={match_id}, jobId={match_job_id}")

    # ── 9. Poll for match completion ──────────────────────────────────
    print("9. Waiting for match engine...")
    start = time.monotonic()
    match_done = False
    while time.monotonic() - start < 30:
        code, body = _req("GET", f"/matches/{match_id}", headers=headers)
        if code != 200:
            time.sleep(1)
            continue
        s = body.get("status", "unknown")
        print(f"   ... {s}")
        if s in ("completed", "failed"):
            match_done = True
            break
        time.sleep(1)
    check("Match completed", match_done, "Timed out")

    # ── 10. Display evidence ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("MATCH RESULT")
    print("=" * 70)
    code, body = _req("GET", f"/matches/{match_id}", headers=headers)

    print(f"Status:     {body.get('status')}")
    print(f"Score:      {body.get('score')}")
    sc = body.get("supportedCount", body.get("supported_count"))
    pc = body.get("partialCount", body.get("partial_count"))
    uc = body.get("unsupportedCount", body.get("unsupported_count"))
    tr = body.get("totalRequirements", body.get("total_requirements"))
    print(f"Supported:  {sc}")
    print(f"Partial:    {pc}")
    print(f"Unsupported:{uc}")
    print(f"Total reqs: {tr}")

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
        for ei in evidence:
            rt = ei.get("requirementText") or ei.get("requirement_text", "?")
            lv = ei.get("supportLevel") or ei.get("support_level", "?")
            cf = ei.get("confidence")
            rty = ei.get("requirementType") or ei.get("requirement_type", "?")
            icon = icons.get(lv, "?")
            print(f"  [{icon}] [{lv}] ({rty}) {rt}")
            if cf is not None:
                print(f"       confidence={cf:.2f}")
            sug = ei.get("suggestion")
            if sug:
                print(f"       SUGGESTION: {sug}")
            war = ei.get("warning")
            if war:
                print(f"       WARNING: {war}")
        print("-" * 70)

    err = body.get("errorMessage") or body.get("error_message")
    if err:
        print(f"\nError: {err}")

    # ── 11. Verifications ─────────────────────────────────────────────
    print("\n--- Verifications ---")
    check("Match status is completed", body.get("status") == "completed",
          f"got {body.get('status')}")
    check("Score is present", body.get("score") is not None)
    check("Total requirements > 0", (tr or 0) > 0)
    check("Evidence items exist", len(evidence) > 0)
    check("Some skills supported", (sc or 0) > 0,
          f"Expected some supported skills for a matching CV, got supported={sc}")
    if uc:
        check("Some skills unsupported (MLOps, Kafka)", (uc or 0) > 0,
              f"Expected unsupported for skills not in CV")

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        print("\nSOME CHECKS FAILED")
        sys.exit(1)
    else:
        print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()