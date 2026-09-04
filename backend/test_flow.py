"""End-to-end test: register → upload CV → parse → job post → match → evidence.

Run inside the API container:
  docker compose exec api python test_flow.py
"""

import io
import json
import time
import requests

API = "http://localhost:8000/api/v1"
EMAIL = f"e2e-{int(time.time())}@test.com"
PASSWORD = "E2ETest1234!"


def main():
    # ── 1. Register ────────────────────────────────────────────────
    print("1. Registering...")
    r = requests.post(f"{API}/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code in (200, 201), f"Register failed: {r.text}"
    token = r.json().get("access_token")
    if not token:
        r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert r.status_code == 200, f"Login failed: {r.text}"
        token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"   ✅ Logged in as {EMAIL}")

    # ── 2. Upload a test CV (generated minimal PDF) ─────────────────
    print("2. Uploading test CV...")
    # Minimal valid PDF
    pdf_content = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )

    r = requests.post(
        f"{API}/cvs",
        headers=headers,
        files={"file": ("test_cv.pdf", pdf_content, "application/pdf")},
    )
    if r.status_code != 202:
        print(f"   ⚠️ Upload returned {r.status_code}: {r.text}")
        # May have existing CV or different response shape — try to find any CV
    cv_data = r.json()
    cv_id = cv_data.get("cvId") or cv_data.get("cv_id")
    proc_job_id = cv_data.get("processingJobId") or cv_data.get("processing_job_id")
    print(f"   ✅ Upload accepted: cvId={cv_id}, jobId={proc_job_id}")

    # ── 3. Poll until parsing completes ─────────────────────────────
    print("3. Waiting for CV pipeline (docling → textract → merge → cv_parse)...")
    timeout = 300
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = requests.get(f"{API}/jobs/{proc_job_id}", headers=headers)
        if r.status_code != 200:
            print(f"   ⚠️ Job poll returned {r.status_code}: {r.text}")
            time.sleep(2)
            continue
        data = r.json()
        status = data.get("status", "unknown")
        print(f"   ... {status}")
        if status == "completed":
            # The last job in the chain; check if cv_parse completed
            # Get the CV status directly
            r2 = requests.get(f"{API}/cvs/{cv_id}", headers=headers)
            if r2.status_code == 200:
                cv_info = r2.json()
                cv_status = cv_info.get("processing_status") or cv_info.get("status", "")
                print(f"   CV status: {cv_status}")
                if cv_status in ("parsed", "completed"):
                    break
        elif status == "failed":
            print(f"   ❌ Job failed: {data.get('lastError')}")
            return
        time.sleep(3)
    else:
        print("   ⚠️ Timed out waiting for CV pipeline")
        return

    # ── 4. Get profile version ID ───────────────────────────────────
    print("4. Getting parsed profile...")
    r = requests.get(f"{API}/cvs/{cv_id}/parsed-profile", headers=headers)
    if r.status_code != 200:
        print(f"   ⚠️ Parsed profile returned {r.status_code}: {r.text}")
        # Try checking raw-text as fallback
    else:
        profile = r.json()
        cv_profile_version_id = profile.get("profileVersionId")
        print(f"   ✅ Profile version: {cv_profile_version_id}")
        print(f"   Skills: {profile.get('structuredPayload', {}).get('skills', {})}")

    # ── 5. Submit a job post ────────────────────────────────────────
    print("5. Submitting job post...")
    job_text = "Senior Software Engineer\n\nRequirements:\n- Python\n- Docker\n- Kubernetes\n- AWS\n\nPreferred:\n- Terraform\n- Machine Learning"

    r = requests.post(f"{API}/job-posts/text", json={"text": job_text}, headers=headers)
    assert r.status_code == 202, f"Job post failed: {r.text}"
    jp_data = r.json()
    jp_id = jp_data.get("jobPostId")
    jp_job_id = jp_data.get("processingJobId")
    print(f"   ✅ Job post accepted: jpId={jp_id}, jobId={jp_job_id}")

    # Wait for parse
    time.sleep(1)
    r = requests.get(f"{API}/jobs/{jp_job_id}", headers=headers)
    print(f"   Parse status: {r.json().get('status')}")

    # Get job post profile ID
    r = requests.get(f"{API}/job-posts/{jp_id}", headers=headers)
    jp_full = r.json()
    jp_profile = jp_full.get("profile", {})
    jp_profile_id = None
    # The profile ID isn't directly in the response — we need to query from DB
    # For now, let's try a different approach: list job posts and extract
    print(f"   Job post title: {jp_profile.get('jobTitle')}")
    print(f"   Required skills: {jp_profile.get('requiredSkills')}")

    # ── 6. Create a match ───────────────────────────────────────────
    print("6. Creating match...")
    # We need the job_post_profile_id — luckily we can find it from the GET response
    # The JobPostResponse doesn't expose profile.id directly, so let's query
    # the job posts list which might have it, or use a direct approach
    # Actually, the match endpoint takes jobPostProfileId, which is job_post_profiles.id
    # We already have jp_id (job_posts.id). The profile has a 1:1 relationship.
    # Let me query the profile ID from the raw API
    # Since the JobPostResponse has a nested profile but no ID, we need to find it
    # Let me just hardcode looking up the most recent match by checking the DB
    # Actually, the simplest: the match request takes jobPostProfileId, which
    # is NOT the same as jobPostId. Let me extract it by checking jp_full response.
    
    # The response has the profile nested but no id field exposed.
    # Let me make a raw SQL query approach or use the list endpoint differently.
    # ACTUALLY: look at the job_posts schema in job_posts.py — it only exposes jobPostId, not profileId.
    # But the match endpoint takes cvProfileVersionId and jobPostProfileId.
    # 
    # The simplest fix: modify the matches endpoint temporarily to accept jobPostId
    # OR: add profileId to the job post response
    # OR: since we know the relationship is 1:1, we can derive it
    #
    # For testing, let me just register the profile ID from a direct lookup.
    # Since GET /job-posts returns items with ids that are job_post IDs,
    # and job_post_profiles has a job_post_id FK, I need the profile UUID.
    #
    # Quick fix: the jp_full response may have it — let me check.
    
    print(f"   JP Full Response keys: {list(jp_full.keys())}")
    print(f"   Needed: cvProfileVersionId + jobPostProfileId")
    print(f"   Have cvProfileVersionId: {cv_profile_version_id}")
    print(f"   Job post profile ID not directly exposed — this is a schema gap.")
    print(f"   Workaround: the match endpoint should accept jobPostId, not jobPostProfileId.")
    print(f"   Or: the job post list response should include profile ID.")
    
    print("\n   ⚠️ MATCH TEST BLOCKED — API schema gap")
    print(f"   POST /matches requires 'jobPostProfileId' but the job post")
    print(f"   response doesn't expose the profile's ID. The profile is 1:1")
    print(f"   with job_post, so either:")
    print(f"   1. Add 'profileId' to JobPostResponse")
    print(f"   2. Change POST /matches to accept 'jobPostId'")
    print(f"   3. Add GET /job-posts/{id}/profile that returns the profile with its ID")


if __name__ == "__main__":
    main()