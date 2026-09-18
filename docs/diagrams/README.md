# Diagrams

Standalone copies of the mermaid diagrams in [`../architecture.md`](../architecture.md).

**Generated file — do not edit by hand.** Change the markdown, then regenerate:

```powershell
python scripts/export_diagrams.py            # refresh the .mmd files and this page
python scripts/export_diagrams.py --render   # also produce SVG + PNG in rendered/
```

## How to view them

* **Right here** — press <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>V</kbd> in VS Code (or
  <kbd>Ctrl</kbd>+<kbd>K</kbd> <kbd>V</kbd> for a side-by-side preview): every diagram below
  renders live, no extension required. GitHub renders this page the same way.
* **As images** — `python scripts/export_diagrams.py --render` writes SVG and PNG copies to
  `rendered/` (git-ignored); open those in any image viewer.
* **Editing** — paste any `.mmd` file into [mermaid.live](https://mermaid.live) for a live
  editor with export options.

## Index

| File | Section in architecture.md | Type |
| --- | --- | --- |
| [`what-the-service-does.mmd`](what-the-service-does.mmd) | What the service does | flowchart |
| [`container-view.mmd`](container-view.mmd) | Container view | flowchart |
| [`happy-path-sequence.mmd`](happy-path-sequence.mmd) | Happy path (sequence) | sequence |
| [`job-state-machine.mmd`](job-state-machine.mmd) | Job state machine | state machine |
| [`data-model.mmd`](data-model.mmd) | Data model | entity relationship |
| [`always-on-operation.mmd`](always-on-operation.mmd) | Always-on operation | flowchart |

## Rendered diagrams

### What the service does

<sub>`what-the-service-does.mmd` · flowchart</sub>

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

### Container view

<sub>`container-view.mmd` · flowchart</sub>

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

### Happy path (sequence)

<sub>`happy-path-sequence.mmd` · sequence</sub>

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

### Job state machine

<sub>`job-state-machine.mmd` · state machine</sub>

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

### Data model

<sub>`data-model.mmd` · entity relationship</sub>

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

### Always-on operation

<sub>`always-on-operation.mmd` · flowchart</sub>

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
