// CV Tailoring — Phase 1 Test Harness
// Plain vanilla JS, no dependencies. Calls real backend endpoints.
// Per 13-frontend-plan.md §2: disposable, minimal, no framework.

const API_BASE = "http://localhost:8000/api/v1";

// ─── State ────────────────────────────────────────────────────────────
let authToken = null;
let userEmail = null;
let currentTab = "upload";
let pollingTimer = null;

// ─── DOM helpers ──────────────────────────────────────────────────────
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }
function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

// ─── API helpers ──────────────────────────────────────────────────────
async function api(method, path, body) {
  const headers = { "Accept": "application/json" };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const opts = { method, headers };
  if (body) opts.body = body instanceof FormData ? body : JSON.stringify(body);
  const res = await fetch(API_BASE + path, opts);
  let data;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const err = new Error(data?.message || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return { status: res.status, data };
}

function showResult(elementId, data, isError) {
  const el = $(`#${elementId}`);
  el.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  el.classList.remove("error", "success", "hidden");
  if (isError) el.classList.add("error");
}

function showError(elementId, err) {
  const msg = err.data ? JSON.stringify(err.data, null, 2) : err.message;
  showResult(elementId, msg, true);
}

// ─── Auth ─────────────────────────────────────────────────────────────
function setAuth(token, email) {
  authToken = token;
  userEmail = email;
  hide($("#auth-section"));
  show($("#app-section"));
  show($("#user-bar"));
  $("#user-email").textContent = email;
  loadCVList();
}

function clearAuth() {
  authToken = null;
  userEmail = null;
  show($("#auth-section"));
  hide($("#app-section"));
  hide($("#user-bar"));
  $("#user-email").textContent = "";
  stopPolling();
}

$("#form-register").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const { data } = await api("POST", "/auth/register", {
      email: fd.get("email"),
      password: fd.get("password"),
    });
    showResult("register-result", data);
    // Auto-login after register
    const login = await api("POST", "/auth/login", {
      email: fd.get("email"),
      password: fd.get("password"),
    });
    setAuth(login.data.accessToken, fd.get("email"));
  } catch (err) { showError("register-result", err); }
});

$("#form-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const { data } = await api("POST", "/auth/login", {
      email: fd.get("email"),
      password: fd.get("password"),
    });
    setAuth(data.accessToken, fd.get("email"));
  } catch (err) { showError("login-result", err); }
});

$("#btn-logout").addEventListener("click", async () => {
  try { await api("POST", "/auth/logout"); } catch {}
  clearAuth();
});

// ─── Tab navigation ───────────────────────────────────────────────────
$$(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    const tabName = btn.dataset.tab;
    $$(".tab").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".tab-content").forEach(c => c.classList.remove("active"));
    $(`#tab-${tabName}`).classList.add("active");
    currentTab = tabName;

    if (tabName === "cv-list") loadCVList();
  });
});

// ─── CV Upload ────────────────────────────────────────────────────────
$("#form-upload").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  const fileInput = e.target.querySelector('input[type="file"]');
  if (!fileInput.files.length) return;
  fd.append("file", fileInput.files[0]);

  try {
    const headers = { "Accept": "application/json" };
    if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
    const res = await fetch(API_BASE + "/cvs", { method: "POST", headers, body: fd });
    const data = await res.json();
    if (!res.ok) {
      showResult("upload-result", data, true);
      return;
    }
    showResult("upload-result", data);
    // Auto-track the new job
    if (data.processingJobId) {
      $("#form-status").querySelector("input").value = data.processingJobId;
    }
    // Load updated list
    setTimeout(loadCVList, 1000);
  } catch (err) { showError("upload-result", err); }
});

// ─── CV List ──────────────────────────────────────────────────────────
async function loadCVList() {
  const container = $("#cv-list-container");
  try {
    const { data } = await api("GET", "/cvs?limit=50");
    if (!data.items || !data.items.length) {
      container.innerHTML = "<p>No CVs uploaded yet.</p>";
      return;
    }
    container.innerHTML = data.items.map(cv => {
      const s = cv.status || "pending";
      const ps = cv.processingStatus || "—";
      const js = cv.jobStatus || "—";
      return `
      <div class="cv-item">
        <div>
          <strong>${esc(cv.originalFilename || cv.filename || "unnamed")}</strong>
          <br><small>${cv.id}</small>
          <br><small style="color: #888">processing: ${ps} | job: ${js}</small>
        </div>
        <span class="status-badge ${s}">${s}</span>
      </div>
      `;
    }).join("");
  } catch (err) {
    container.innerHTML = `<p class="result error">Failed to load: ${esc(err.message)}</p>`;
  }
}

$("#btn-refresh-cvs").addEventListener("click", loadCVList);

// ─── Job Status Tracking ──────────────────────────────────────────────
$("#form-status").addEventListener("submit", (e) => {
  e.preventDefault();
  const jobId = new FormData(e.target).get("jobId");
  startPolling(jobId);
});

function startPolling(jobId) {
  stopPolling();
  pollJob(jobId);
  if ($("#auto-poll").checked) {
    pollingTimer = setInterval(() => pollJob(jobId), 2000);
  }
}

function stopPolling() {
  if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; }
}

async function pollJob(jobId) {
  try {
    const { data } = await api("GET", `/jobs/${jobId}`);
    show($("#status-display"));
    hide($("#status-result"));

    const status = data.status || "unknown";
    const statusEl = $("#status-value");
    statusEl.textContent = status;
    statusEl.className = status; // completed / failed / processing

    $("#s-job-type").textContent = data.jobType || "—";
    $("#s-status").textContent = status;
    $("#s-retries").textContent = data.retryCount ?? "—";
    $("#s-error").textContent = data.lastError || "—";
    $("#s-created").textContent = data.createdAt || "—";
    $("#s-started").textContent = data.startedAt || "—";
    $("#s-completed").textContent = data.completedAt || "—";

    if (status === "completed" || status === "failed") {
      stopPolling();
    }
  } catch (err) {
    showResult("status-result", `Error: ${err.message}`, true);
    stopPolling();
  }
}

// ─── Extraction Result ────────────────────────────────────────────────
$("#form-result").addEventListener("submit", async (e) => {
  e.preventDefault();
  const cvId = new FormData(e.target).get("cvId");
  try {
    const [rawRes, detailRes] = await Promise.all([
      api("GET", `/cvs/${cvId}/raw-text`),
      api("GET", `/cvs/${cvId}/extraction-detail`),
    ]);

    show($("#extraction-output"));
    hide($("#result-result"));

    $("#raw-text-display").textContent = rawRes.data?.canonicalText || "(empty)";

    $("#extraction-detail-display").textContent = JSON.stringify(
      detailRes.data, null, 2
    );
  } catch (err) {
    showResult("result-result", `Error: ${err.message}`, true);
  }
});

// ─── Parsed Profile (Phase 2) ────────────────────────────────────────

$("#form-parsed").addEventListener("submit", async (e) => {
  e.preventDefault();
  const cvId = new FormData(e.target).get("cvId");
  try {
    const { data } = await api("GET", `/cvs/${cvId}/parsed-profile`);
    showResult("parsed-result", data);
  } catch (err) { showError("parsed-result", err); }
});

// ─── Job Posts (Phase 2) ─────────────────────────────────────────────

$("#form-job-url").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = new FormData(e.target).get("url");
  try {
    const { data } = await api("POST", "/job-posts/url", { url });
    showResult("job-url-result", data);
    setTimeout(loadJobList, 1000);
  } catch (err) { showError("job-url-result", err); }
});

$("#form-job-text").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = new FormData(e.target).get("text");
  try {
    const { data } = await api("POST", "/job-posts/text", { text });
    showResult("job-text-result", data);
    setTimeout(loadJobList, 1000);
  } catch (err) { showError("job-text-result", err); }
});

async function loadJobList() {
  const container = $("#job-list-container");
  try {
    const { data } = await api("GET", "/job-posts?limit=50");
    if (!data.items || !data.items.length) {
      container.innerHTML = "<p>No job posts yet.</p>";
      return;
    }
    container.innerHTML = data.items.map(jp => `
      <div class="cv-item">
        <div>
          <strong>${esc(jp.sourceType === "url" ? "🌐 " + (jp.sourceUrl || "") : "📋 Pasted text")}</strong>
          <br><small>ID: ${jp.id}</small>
          ${jp.profile ? `<br><small>Title: ${esc(jp.profile.jobTitle || "—")} | Skills: ${(jp.profile.requiredSkills || []).length + (jp.profile.preferredSkills || []).length}</small>` : ""}
        </div>
        <span class="status-badge ${jp.status || "pending"}">${jp.status || "pending"}</span>
      </div>
    `).join("");
  } catch (err) {
    container.innerHTML = `<p class="result error">Failed to load: ${esc(err.message)}</p>`;
  }
}

$("#btn-refresh-jobs").addEventListener("click", loadJobList);

// ─── Utilities ────────────────────────────────────────────────────────
function esc(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}