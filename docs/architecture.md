# KYC Grabber — architecture

> Status: framework/stub. The internal database and the external registry are **simulated**;
> every payload they return is marked as synthetic demo data.

## 1. What the service does

```mermaid
flowchart LR
    A["Analyst sends an email<br/>with a CSV of third-party codes"] --> B["KYC Grabber<br/>(always running)"]
    B --> C["Internal database<br/>lookup"]
    B --> D["External registry /<br/>screening lookup"]
    C --> E["Reconcile &amp; score risk"]
    D --> E
    E --> F["Excel workbook<br/>1 sheet per third party"]
    F --> G["Reply email<br/>with the workbook attached"]
```

## 2. Container view

```mermaid
flowchart TB
    subgraph mail["Mail boundary"]
        IMAAP["IMAP mailbox<br/>(production)"]
        INBOX[("data/inbox<br/>*.eml drop-ins<br/>(dev / demo)")]
    end

    subgraph svc["KYC Grabber process (single asyncio event loop)"]
        direction TB
        SRC["Mail source<br/><code>watch/</code><br/>IMAP IDLE + poll fallback<br/>or filesystem watcher"]
        FILT["MailFilter<br/><code>watch/filter.py</code><br/>sender allow-list, subject token,<br/>attachment policy"]
        PARSE["CSV parser<br/><code>csv_parser.py</code>"]
        PIPE["Pipeline<br/><code>pipeline.py</code>"]
        Q["Job queue"]
        W1["Worker 1"]
        W2["Worker N"]
        INT["Internal client<br/><code>clients/internal_db.py</code>"]
        EXT["External client<br/><code>clients/external_db.py</code><br/>retry + backoff + cache"]
        XL["Excel writer<br/><code>excel_report.py</code>"]
        OUT["Outbound mailer<br/><code>mail/sender.py</code><br/>SMTP or outbox"]
        BUS["Event bus<br/><code>bus.py</code>"]
        API["FastAPI<br/><code>web/app.py</code><br/>REST + SSE"]
    end

    subgraph store["Storage"]
        DB[("SQLite<br/>jobs · job_items · events · mail_seen")]
        FILES[("data/out/*.xlsx<br/>data/outbox/*.eml")]
    end

    subgraph ui["Operators"]
        DASH["Dashboard<br/><code>web/static/</code>"]
    end

    IMAAP --> SRC
    INBOX --> SRC
    SRC --> FILT --> PARSE --> PIPE
    PIPE --> Q --> W1 & W2
    W1 & W2 --> INT
    W1 & W2 --> EXT
    W1 & W2 --> XL --> FILES
    W1 & W2 --> OUT
    PIPE --> DB
    W1 & W2 --> DB
    PIPE & W1 & W2 --> BUS --> API
    API <--> DB
    API --> FILES
    DASH <-->|REST + SSE| API
```

## 3. Happy path (sequence)

```mermaid
sequenceDiagram
    autonumber
    participant U as Analyst
    participant M as Mailbox
    participant S as Mail source
    participant P as Pipeline
    participant D as Internal DB
    participant X as External registry
    participant E as Excel writer
    participant O as Outbound mailer

    U->>M: email "[KYC] …" + third_parties.csv
    M-->>S: IDLE notification / poll hit
    S->>S: parse RFC822 -> RawMail
    S->>P: ingest(RawMail)
    P->>P: dedupe by Message-ID
    P->>P: filter (sender, subject token, .csv, size)
    P->>P: parse codes, dedupe rows, validate
    P->>P: create job + items (SQLite), publish event
    P->>D: lookup(code) per item
    D-->>P: internal record or miss
    P->>X: fetch(code, hints) per item
    X-->>P: external record / not-found / error
    P->>P: compare() -> match status + differences
    P->>P: derive_risk() -> low..critical
    P->>E: write one sheet per third party
    E-->>P: report.xlsx
    P->>O: reply with attachment
    O-->>U: "KYC report for third_parties.csv (6 third parties)"
    P->>P: mark job completed, publish events
```

## 4. Job state machine

```mermaid
stateDiagram-v2
    [*] --> received
    received --> rejected: filter/parse failure (reply with reason)
    received --> queued: codes accepted
    queued --> enriching: worker picks it up
    enriching --> rendering: all codes enriched
    enriching --> failed: unrecoverable error
    rendering --> delivering
    delivering --> completed
    failed --> [*]
    completed --> [*]
    rejected --> [*]
    completed --> queued: operator re-run
```

Per third-party outcome (`job_items.match_status`):

| Value | Meaning |
| --- | --- |
| `match` | Internal and external records agree on name, country and entity type |
| `mismatch` | Records exist but differ — differences are listed in the sheet |
| `internal_only` | Known internally, no external registry record |
| `external_only` | Found externally but missing from the internal master data |
| `not_found` | Present in neither source |
| `error` | A source lookup failed (timeout, provider outage) |

## 5. Data model

```mermaid
erDiagram
    JOBS ||--o{ JOB_ITEMS : "has"
    JOBS ||--o{ EVENTS : "logs"
    MAIL_SEEN ||--o| JOBS : "dedupe"

    JOBS {
        text id PK "J-YYYYMMDD-HHMMSS-XXXX"
        text status "received|queued|enriching|rendering|delivering|completed|failed|rejected"
        text sender
        text subject
        text message_id
        text filename
        int total_codes
        int processed_codes
        int failed_codes
        int warnings
        text workbook_path
        text delivered_to
        text delivery_mode
        text reject_reason
        text created_at
        text updated_at
    }
    JOB_ITEMS {
        text job_id PK,FK
        text code PK
        text status
        text match_status
        text risk_rating
        text internal_json
        text external_json
        text differences
        text warnings
        text error
        int duration_ms
    }
    EVENTS {
        int id PK
        text job_id FK
        text at
        text level
        text message
        text payload
    }
    MAIL_SEEN {
        text message_id PK
        text job_id
        text seen_at
    }
```

## 6. Always-on operation

```mermaid
flowchart LR
    subgraph proc["One process, one event loop"]
        SUP["Supervisor<br/><code>supervisor.py</code>"]
        SUP -->|task| MAILT["Mail source loop<br/>(thread for IMAP)"]
        SUP -->|task| WT["Worker pool"]
        SUP -->|task| WEBT["uvicorn server"]
        SUP -->|task| HK["Housekeeping<br/>prune events · heartbeat"]
    end
    SUP -->|SIGINT / SIGTERM| GRACE["Graceful shutdown:<br/>stop source, cancel tasks, close store"]
```

Resilience rules baked into the framework:

* the mail source reconnects with exponential backoff on any IMAP/network error;
* a corrupt or unparseable email is archived and skipped, never crashing the watcher;
* per-code lookups are isolated — a failing code produces an `error` row, the job still completes;
* the external client retries transient failures with backoff and caches results;
* the worker pool never dies because of a single bad job;
* re-delivered emails are ignored via the `mail_seen` dedupe table (Message-ID).

## 7. Where real integrations plug in

| Component | File | Replace with |
| --- | --- | --- |
| Internal database | `kyc_grabber/clients/internal_db.py` | SQLAlchemy/pyodbc/HTTP client against the real master data. Drop the latency simulation. |
| External registry | `kyc_grabber/clients/external_db.py` | Real provider call (KYC/registry API, Dow Jones, Refinitiv…). Keep retry/backoff/cache/concurrency; map the payload to `ExternalRecord`. |
| Mail intake | `kyc_grabber/watch/imap_source.py` | Point `KYC_MAIL__IMAP__*` at the real mailbox; swap the password for OAuth2 (XOAUTH2) if required. |
| Delivery | `kyc_grabber/mail/sender.py` | Set `KYC_OUTBOUND__MODE=smtp` and the relay settings. |
| Reconciliation rules | `kyc_grabber/analysis.py` | Tighten name matching, add field-level rules, wire a risk engine. |
| Report layout | `kyc_grabber/excel_report.py` | Adjust sections/columns; keep the one-sheet-per-third-party contract. |

## 8. Threat-model notes (for the production build)

* Credentials must live in `.env`/a secret store, never in Git (`.env` is git-ignored; only `.env.example` is committed).
* Inbound mail is untrusted input: the filter restricts senders, the CSV parser validates codes and caps size/count.
* The dashboard is bound to localhost by default; put it behind SSO/reverse proxy before exposing it.
* The workbook may contain personal data (directors, PEP flags) — outbound email should be encrypted (S/MIME or a secure link) once real data is used.
