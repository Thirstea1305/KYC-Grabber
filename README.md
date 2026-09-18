# KYC Grabber

An always-running service that turns an email into a KYC report.

**Email in (CSV of third-party codes) → internal DB check → external registry check → multi-sheet Excel out.**

* Architecture & diagrams: [`docs/architecture.md`](docs/architecture.md)
* Dashboard: `http://127.0.0.1:8080` once the service is running

> ⚠️ This is a **framework**. The internal database and the external registry are simulated and every
> record they return is synthetic demo data. See [Plugging in real integrations](#plugging-in-real-integrations).

---

## 1. Quickstart

```powershell
# from the project root (d:\Projects\kyc-grabber)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env          # optional: tweak ports, mailboxes, limits

# 1) drop a sample KYC email into the watcher inbox
.\.venv\Scripts\python.exe run.py sample

# 2) start the always-on service (watcher + workers + dashboard)
.\.venv\Scripts\python.exe run.py run
```

> `python run.py <command>` is equivalent to `python -m kyc_grabber <command>` but works from any
> working directory. `python -m kyc_grabber` requires the current directory to be the project root.

Open <http://127.0.0.1:8080>. The dashboard shows the watcher state, live logs and the job table;
the sample email is picked up within a couple of seconds and produces:

* `data/out/kyc-report-<job>.xlsx` — one worksheet per third party + a summary sheet
* `data/outbox/reply-<job>.eml` — the reply email with the workbook attached

You can also trigger everything from the dashboard: **Simulate an inbound email → Send email**.

### One-off processing

```powershell
.\.venv\Scripts\python.exe run.py process .\data\inbox\some-request.eml
```

### Testing the filter rules

```powershell
.\\.venv\\Scripts\\python.exe scripts\send_email.py --csv-file .\my_codes.csv   # should be accepted
.\\.venv\\Scripts\\python.exe scripts\send_email.py --subject "no token"       # should be rejected
.\\.venv\\Scripts\\python.exe scripts\send_email.py --no-attachment             # should be rejected
```

### Other modes

| Command | What it does |
| --- | --- |
| `python -m kyc_grabber run` | watcher + workers + dashboard (the normal deployment) |
| `python -m kyc_grabber run --no-web` | headless watcher + workers |
| `python -m kyc_grabber run --source imap` | force the IMAP source instead of the inbox watcher |
| `python -m kyc_grabber web` | dashboard + workers only (no mail intake) |
| `python -m kyc_grabber process <file.eml>` | process one email and exit |
| `python -m kyc_grabber sample` | write a sample KYC email into the inbox |

---

## 2. How it works

```
IMAP mailbox (prod) ─┐
                     ├─► MailFilter ─► CSV parser ─► job (SQLite) ─► worker pool ─┬─► Internal DB client
data/inbox/*.eml ────┘   sender/subject   codes +       items + events             └─► External DB client
                         /attachment      validation                                    │
                                                                          Excel writer ◄┘   (1 sheet per code)
                                                                                 │
                                                             Outbound mailer ◄───┘  (reply with attachment)
```

1. **Intake** — a mail source pushes `RawMail` objects onto a queue. Two implementations ship:
   `FilesystemMailSource` (watches `data/inbox/*.eml`, used for the demo and for tests) and
   `ImapMailSource` (IMAP IDLE with polling fallback, automatic reconnect).
2. **Filter** — sender allow-list (`*`, `user@x.com`, `@domain.com`, globs, `re:…`), required subject
   token (default `[KYC]`), attachment extension/size limits. Rejections are recorded as jobs and
   optionally answered with an explanatory email.
3. **Parse** — the CSV parser finds the code column by header name (`third_party_code`, `code`,
   `vendor_code`, `id`, …) or falls back to the first column, tolerates `, ; \t |` delimiters, UTF-8
   BOM and cp1252, de-duplicates rows and reports invalid codes.
4. **Internal check** — `InternalDatabaseClient` (simulated JSON master data).
5. **External check** — `ExternalDatabaseClient` (simulated registry with latency, jitter, transient
   failures, retries with backoff, not-found responses, caching and bounded concurrency). Payloads are
   deterministic per code, so demos and tests are reproducible.
6. **Reconcile** — `analysis.compare()` normalises legal names (accents, punctuation, legal suffixes)
   and produces `match` / `mismatch` / `internal_only` / `external_only` / `not_found` / `error`
   plus a field-level difference list. `analysis.derive_risk()` escalates to `critical` on sanctions
   hits and to `high` on PEP/adverse-media flags.
7. **Report** — `excel_report.py` writes a styled workbook: a summary sheet (all codes, sorted with
   the riskiest first) and **one worksheet per third party** containing the reconciliation outcome,
   the internal record, the external record, directors, shareholders, adverse media, differences and
   the raw payloads.
8. **Deliver** — the workbook is attached to a reply message (`filesystem` outbox by default, `smtp`
   when configured) together with a plain-text summary.

Every step publishes an event that is persisted in SQLite and streamed to the dashboard over SSE.

---

## 3. Configuration

All settings come from the environment with the `KYC_` prefix; nested keys use `__`
(e.g. `KYC_MAIL__IMAP__HOST`). Copy [`.env.example`](.env.example) to `.env` — that file is git-ignored.

| Setting | Default | Purpose |
| --- | --- | --- |
| `KYC_MAIL__MODE` | `filesystem` | `filesystem` (inbox watcher) or `imap` |
| `KYC_MAIL__INBOX_DIR` | `data/inbox` | where `*.eml` drop-ins are watched |
| `KYC_MAIL__POLL_INTERVAL_SECONDS` | `5` | watcher poll interval |
| `KYC_FILTER__ALLOWED_SENDERS` | `*` | allow-list: exact, `@domain`, glob, `re:…`, `*` |
| `KYC_FILTER__SUBJECT_TOKEN` | `[KYC]` | subject must contain this token (`""` = any) |
| `KYC_FILTER__MAX_CODES_PER_REQUEST` | `500` | reject bigger lists (ask the sender to split) |
| `KYC_FILTER__REPLY_ON_REJECT` | `true` | answer rejected emails with the reason |
| `KYC_MAIL__IMAP__*` | placeholders | host, port, TLS, credentials, mailboxes, IDLE timeout |
| `KYC_OUTBOUND__MODE` | `filesystem` | `filesystem` (write `.eml`) or `smtp` |
| `KYC_INTERNAL__SEED_PATH` | `data/internal_db_seed.json` | simulated internal master data |
| `KYC_EXTERNAL__FAILURE_RATE` / `NOT_FOUND_RATE` | `0.03` / `0.12` | simulate provider behaviour |
| `KYC_EXTERNAL__CONCURRENCY` | `8` | max parallel external lookups |
| `KYC_PIPELINE__WORKERS` | `2` | job workers |
| `KYC_PIPELINE__CODE_CONCURRENCY` | `6` | codes enriched in parallel per job |
| `KYC_EXCEL__INCLUDE_SUMMARY_SHEET` | `true` | add the cover sheet |
| `KYC_EXCEL__INCLUDE_RAW_PAYLOAD` | `true` | append raw JSON payloads per sheet |
| `KYC_WEB__HOST` / `KYC_WEB__PORT` | `127.0.0.1` / `8080` | dashboard binding |

`GET /api/status` returns the effective (credential-free) configuration.

---

## 4. Dashboard & API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | operations dashboard |
| `GET` | `/api/status` | watcher/worker/data-source health and effective config |
| `GET` | `/api/jobs?limit=25` | recent jobs with progress |
| `GET` | `/api/jobs/{id}` | job detail incl. per-third-party results and events |
| `GET` | `/api/jobs/{id}/workbook` | download the Excel report |
| `POST` | `/api/jobs/{id}/rerun` | re-queue a job |
| `GET` | `/api/events?limit=200[&job_id=]` | event history |
| `GET` | `/api/events/stream` | live events (SSE) |
| `POST` | `/api/simulate/email` | inject an email (JSON: sender, subject, filename, csv_text) |
| `POST` | `/api/simulate/email/upload` | inject an email with an uploaded CSV |
| `GET` | `/api/sample/codes.csv` | sample CSV payload |
| `POST` | `/api/external/cache/clear` | drop the simulated external cache |
| `GET` | `/api/inbox` | pending/processed drop-ins |
| `GET` | `/api/docs` | OpenAPI UI |

---

## 5. Project layout

```
kyc_grabber/
  __main__.py        CLI (run / web / process / sample)
  config.py          environment-driven settings
  models.py          domain models and enums
  store.py           SQLite: jobs, job_items, events, mail_seen
  bus.py             in-process pub/sub for live events
  pipeline.py        the four workflow steps
  analysis.py        reconciliation + risk scoring
  excel_report.py    multi-sheet workbook writer
  worker.py          bounded job worker pool
  supervisor.py      always-on supervisor (tasks, signals, housekeeping)
  runtime.py         application context wiring
  csv_parser.py      tolerant CSV code extraction
  samples.py         demo payloads
  clients/           internal_db.py (simulated), external_db.py (simulated)
  mail/              composer.py (RFC822), sender.py (outbound)
  watch/             base.py, filesystem_source.py, imap_source.py, filter.py
  web/               app.py (FastAPI), static/ (dashboard)
run.py               convenience launcher (any working directory)
scripts/             send_email.py - drop a test request into the mailbox
data/                internal seed, inbox, outbox, out/, sqlite store
deploy/              systemd unit, docker files, always-on instructions
docs/                architecture.md (diagrams)
tests/               57 tests, no external services required
```

---

## 6. Plugging in real integrations

| Want | Do |
| --- | --- |
| Real internal database | Reimplement `InternalDatabaseClient.lookup()` (SQL/HTTP). The pipeline only needs `lookup()` and `record_count`. |
| Real external provider | Reimplement `ExternalDatabaseClient.fetch()`; keep the retry/backoff/cache/concurrency wrapper and map the response to `ExternalRecord`. |
| Real mailbox | Set `KYC_MAIL__MODE=imap` and fill `KYC_MAIL__IMAP__*`; use an app password or OAuth2 (`XOAUTH2`). |
| Real email delivery | Set `KYC_OUTBOUND__MODE=smtp` and fill `KYC_OUTBOUND__SMTP__*`. |
| Different sheet layout | Edit `_build_detail_sheet()` in `excel_report.py`; the one-sheet-per-third-party contract is enforced by `build_workbook()`. |

---

## 7. Testing

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite covers CSV parsing, mail filtering, RFC822 round-trips, reconciliation/risk logic, workbook
generation and the full pipeline (including the always-on watcher picking up a dropped email and the
external-outage path). No network or credentials are required.

---

## 8. Known limitations / next steps

* Internal and external data sources are simulations — no real lookups yet.
* No authentication on the dashboard (localhost only) and no per-user audit trail.
* Risk scoring is deliberately simple (max of the declared rating and screening flags).
* The workbook is plain-text (no encryption) and contains personal data (directors, PEP flags) once
  real sources are wired in.
* `mail_seen` grows without pruning; a retention job is planned alongside real deployment.
