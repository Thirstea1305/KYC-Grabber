/* KYC Grabber dashboard - vanilla JS, no build step. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MAX_LOG_LINES = 400;
  const state = { selectedJob: null, jobs: [], pollTimer: null };

  const JOB_KIND = {
    completed: "ok", failed: "err", rejected: "warn",
    received: "info", queued: "info", enriching: "info",
    rendering: "info", delivering: "info",
  };
  const MATCH_KIND = {
    match: "ok", mismatch: "warn", not_found: "muted",
    internal_only: "info", external_only: "info", error: "err",
  };
  const RISK_KIND = { critical: "err", high: "err", medium: "warn", low: "ok", unrated: "muted" };

  // ------------------------------------------------------------------ helpers
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = { detail: text }; }
    if (!response.ok) throw new Error((body && body.detail) || `${response.status} ${response.statusText}`);
    return body;
  }

  const fmtTime = (iso) => (iso ? iso.replace("T", " ").slice(0, 19) : "-");
  const fmtDuration = (seconds) => {
    const s = Math.round(seconds || 0);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  };

  function badge(text, kind) {
    const span = document.createElement("span");
    span.className = `badge badge-${kind || "muted"}`;
    span.textContent = text;
    return span;
  }

  // ------------------------------------------------------------------- status
  async function refreshStatus() {
    try {
      const status = await api("/api/status");
      const source = status.source || {};
      const worker = status.worker || {};
      const data = status.data || {};
      const jobs = data.jobs || {};

      const pill = $("servicePill");
      pill.textContent = source.running && worker.running ? "service running" : "service degraded";
      pill.className = `pill ${source.running && worker.running ? "pill-ok" : "pill-err"}`;
      $("uptime").textContent = `uptime ${fmtDuration(status.uptime_seconds)} · ${status.environment}`;

      $("statWatcher").textContent = source.name || "-";
      $("statWatcherSub").textContent = source.running
        ? `watching · queue ${source.queue_depth}`
        : (source.last_error || "stopped");

      $("statMails").textContent = source.mails_seen ?? 0;
      $("statPolled").textContent = `last poll ${fmtTime(source.last_poll_at)}`;

      $("statQueued").textContent = worker.queued ?? 0;
      $("statWorkers").textContent = `${worker.active} active / ${worker.workers} workers · ${worker.processed} done`;

      $("statDone").textContent = `${jobs.completed || 0} / ${jobs.failed || 0}`;
      $("statRejected").textContent = `rejected ${jobs.rejected || 0}`;

      $("statInternal").textContent = (data.internal_records ?? 0).toLocaleString();
      const external = data.external || {};
      $("statExternal").textContent = `${external.calls || 0} calls · ${external.transient_failures || 0} failures`;
      $("statCache").textContent = `${external.cached_codes || 0} codes`;
    } catch (error) {
      const pill = $("servicePill");
      pill.textContent = "service unreachable";
      pill.className = "pill pill-err";
    }
  }

  // --------------------------------------------------------------------- jobs
  async function refreshJobs() {
    try {
      const payload = await api("/api/jobs?limit=25");
      state.jobs = payload.jobs || [];
      renderJobs(state.jobs);
      if (state.selectedJob) await openJob(state.selectedJob, true);
    } catch { /* the status card already surfaces connectivity problems */ }
  }

  function renderJobs(jobs) {
    const tbody = $("jobsTable").querySelector("tbody");
    tbody.replaceChildren();
    $("jobCount").textContent = `${jobs.length} most recent`;

    if (!jobs.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 8;
      td.className = "muted";
      td.textContent = "No jobs yet - send a simulated email to get started.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    for (const job of jobs) {
      const tr = document.createElement("tr");

      const idCell = document.createElement("td");
      const link = document.createElement("button");
      link.className = "link-btn";
      link.textContent = job.id;
      link.addEventListener("click", () => openJob(job.id));
      idCell.appendChild(link);
      tr.appendChild(idCell);

      const statusCell = document.createElement("td");
      statusCell.appendChild(badge(job.status, JOB_KIND[job.status]));
      tr.appendChild(statusCell);

      const appendText = (value, className) => {
        const td = document.createElement("td");
        if (className) td.className = className;
        td.textContent = value;
        tr.appendChild(td);
      };
      appendText(job.sender || "-");
      appendText(job.subject || job.reject_reason || "-", "wrap");

      appendText(`${job.processed_codes}/${job.total_codes}${job.failed_codes ? ` (${job.failed_codes} err)` : ""}`);

      const progressCell = document.createElement("td");
      const bar = document.createElement("span");
      bar.className = "progress";
      const fill = document.createElement("i");
      fill.style.width = `${Math.min(100, job.progress || 0)}%`;
      bar.appendChild(fill);
      progressCell.appendChild(bar);
      tr.appendChild(progressCell);

      appendText(fmtTime(job.created_at));

      const actionCell = document.createElement("td");
      if (job.workbook_path) {
        const download = document.createElement("a");
        download.className = "link-btn";
        download.href = `/api/jobs/${encodeURIComponent(job.id)}/workbook`;
        download.textContent = "xlsx";
        actionCell.appendChild(download);
      }
      tr.appendChild(actionCell);

      tbody.appendChild(tr);
    }
  }

  // ------------------------------------------------------------- job detail
  async function openJob(jobId, silent = false) {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      state.selectedJob = jobId;
      $("detailPanel").hidden = false;
      $("detailTitle").textContent = `Job ${job.id}`;

      const download = $("downloadBtn");
      if (job.workbook_path) {
        download.hidden = false;
        download.href = `/api/jobs/${encodeURIComponent(job.id)}/workbook`;
      } else {
        download.hidden = true;
      }

      const meta = $("detailMeta");
      meta.replaceChildren();
      const fields = [
        ["Status", job.status],
        ["From", job.sender],
        ["Subject", job.subject],
        ["Source file", job.filename || "-"],
        ["Codes", `${job.processed_codes}/${job.total_codes} (${job.failed_codes} failed, ${job.warnings} warnings)`],
        ["Created", fmtTime(job.created_at)],
        ["Delivered to", job.delivered_to ? `${job.delivered_to} via ${job.delivery_mode}` : "not delivered"],
        ["Error / reason", job.error || job.reject_reason || "none"],
      ];
      for (const [label, value] of fields) {
        const wrapper = document.createElement("div");
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = value ?? "-";
        wrapper.append(dt, dd);
        meta.appendChild(wrapper);
      }
      renderItems(job.items || []);
    } catch (error) {
      if (!silent) appendLog({ level: "error", message: `Could not load job: ${error.message}`, at: new Date().toISOString() });
    }
  }

  function renderItems(items) {
    const tbody = $("itemsTable").querySelector("tbody");
    tbody.replaceChildren();
    if (!items.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "muted";
      td.textContent = "No third-party results yet.";
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    for (const item of items) {
      const tr = document.createElement("tr");
      const cell = (value, className) => {
        const td = document.createElement("td");
        if (className) td.className = className;
        td.textContent = value ?? "-";
        tr.appendChild(td);
        return td;
      };
      cell(item.code);
      const matchCell = cell("");
      matchCell.appendChild(badge(item.match_status || item.status, MATCH_KIND[item.match_status]));
      const riskCell = cell("");
      riskCell.appendChild(badge(item.risk_rating || "unrated", RISK_KIND[item.risk_rating]));
      cell(item.internal ? item.internal.legal_name : "not found", "wrap");
      cell(item.external ? item.external.legal_name : "not found", "wrap");
      const notes = [...(item.differences || []), ...(item.warnings || []), item.error]
        .filter(Boolean).join(" | ") || "-";
      cell(notes, "wrap");
      tbody.appendChild(tr);
    }
  }

  // ---------------------------------------------------------------------- log
  function appendLog(event) {
    const container = $("log");
    const line = document.createElement("div");
    line.className = `log-line level-${(event.level || "info").toLowerCase()}`;

    const time = document.createElement("span");
    time.className = "t";
    time.textContent = (event.at || "").slice(11, 19) || "--:--:--";

    const level = document.createElement("span");
    level.className = "l";
    level.textContent = event.level || "info";

    const message = document.createElement("span");
    message.className = "log-msg";
    message.textContent = (event.job_id ? `[${event.job_id}] ` : "") + (event.message || "");

    line.append(time, level, message);
    container.appendChild(line);
    while (container.childElementCount > MAX_LOG_LINES) container.removeChild(container.firstChild);
    container.scrollTop = container.scrollHeight;

    if (event.payload && event.payload.total && event.payload.processed === event.payload.total) refreshJobs();
  }

  function connectStream() {
    const source = new EventSource("/api/events/stream");
    source.addEventListener("log", (message) => {
      try { appendLog(JSON.parse(message.data)); } catch { /* ignore malformed frames */ }
    });
    source.addEventListener("error", () => {
      // EventSource reconnects automatically; nothing to do.
    });
    return source;
  }

  // ----------------------------------------------------------------- simulate
  async function sendSimulated(event) {
    event.preventDefault();
    const button = $("sendBtn");
    const result = $("simResult");
    button.disabled = true;
    result.textContent = "sending…";
    try {
      const file = $("simFile").files[0];
      let payload;
      if (file) {
        const data = new FormData();
        data.append("sender", $("simSender").value);
        data.append("subject", $("simSubject").value);
        data.append("file", file);
        payload = await api("/api/simulate/email/upload", { method: "POST", body: data });
      } else {
        payload = await api("/api/simulate/email", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sender: $("simSender").value,
            subject: $("simSubject").value,
            filename: $("simFilename").value,
            csv_text: $("simCsv").value,
          }),
        });
      }
      result.textContent = payload.detail || "email injected";
      setTimeout(refreshJobs, 1200);
      setTimeout(refreshJobs, 4000);
    } catch (error) {
      result.textContent = `failed: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  }

  async function loadSample() {
    try {
      const response = await fetch("/api/sample/codes.csv");
      $("simCsv").value = await response.text();
    } catch { /* ignore */ }
  }

  // --------------------------------------------------------------------- init
  async function init() {
    $("simForm").addEventListener("submit", sendSimulated);
    $("sampleBtn").addEventListener("click", loadSample);
    $("clearLogBtn").addEventListener("click", () => $("log").replaceChildren());
    $("closeDetailBtn").addEventListener("click", () => {
      $("detailPanel").hidden = true;
      state.selectedJob = null;
    });
    $("rerunBtn").addEventListener("click", async () => {
      if (!state.selectedJob) return;
      try {
        await api(`/api/jobs/${encodeURIComponent(state.selectedJob)}/rerun`, { method: "POST" });
        setTimeout(refreshJobs, 800);
      } catch (error) {
        appendLog({ level: "error", message: `Re-run failed: ${error.message}`, at: new Date().toISOString() });
      }
    });
    $("clearCacheBtn").addEventListener("click", async () => {
      try {
        const payload = await api("/api/external/cache/clear", { method: "POST" });
        appendLog({ level: "info", message: `External cache cleared (${payload.cleared} entries)`, at: new Date().toISOString() });
        refreshStatus();
      } catch { /* ignore */ }
    });

    await loadSample();
    await refreshStatus();
    await refreshJobs();
    connectStream();
    try {
      const recent = await api("/api/events?limit=40");
      (recent.events || []).forEach(appendLog);
    } catch { /* ignore */ }

    state.pollTimer = setInterval(() => { refreshStatus(); refreshJobs(); }, 5000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
