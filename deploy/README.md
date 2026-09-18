# Deployment — running KYC Grabber as an always-on service

The service is a single long-running process (`python -m kyc_grabber run`) that hosts the mail watcher,
the worker pool and the dashboard. Pick whichever supervisor matches your infrastructure.

---

## Option A — Windows: Task Scheduler

Startup-triggered task that runs the process at boot, restarts on failure and runs even when nobody is
logged in (requires the task to run as a service account or with stored credentials).

```powershell
$action  = New-ScheduledTaskAction -Execute "d:\Projects\kyc-grabber\.venv\Scripts\python.exe" `
                                   -Argument "-m kyc_grabber run" `
                                   -WorkingDirectory "d:\Projects\kyc-grabber"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                                         -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 0)
Register-ScheduledTask -TaskName "KYC Grabber" -Action $action -Trigger $trigger -Settings $settings `
                       -Description "Email-driven KYC enrichment service" -RunLevel Highest
```

Check it:

```powershell
Get-ScheduledTask -TaskName "KYC Grabber" | Get-ScheduledTaskInfo
Start-ScheduledTask -TaskName "KYC Grabber"
```

## Option B — Windows: NSSM (true service, cleaner restarts)

```powershell
nssm install KycGrabber "d:\Projects\kyc-grabber\.venv\Scripts\python.exe" "-m" "kyc_grabber" "run"
nssm set KycGrabber AppDirectory "d:\Projects\kyc-grabber"
nssm set KycGrabber AppStdout "d:\Projects\kyc-grabber\logs\service.log"
nssm set KycGrabber AppStderr "d:\Projects\kyc-grabber\logs\service.err.log"
nssm set KycGrabber AppExit Default Restart
nssm set KycGrabber Start SERVICE_AUTO_START
nssm start KycGrabber
```

## Option C — `run-windows.ps1`

A self-restarting wrapper for interactive/hosted use, with timestamped log files:

```powershell
.\deploy\run-windows.ps1
```

## Option D — Linux: systemd

```bash
sudo useradd --system --home /opt/kyc-grabber kyc
sudo cp -r . /opt/kyc-grabber && sudo chown -R kyc:kyc /opt/kyc-grabber
sudo -u kyc python3 -m venv /opt/kyc-grabber/.venv
sudo -u kyc /opt/kyc-grabber/.venv/bin/pip install -r /opt/kyc-grabber/requirements.txt
sudo cp deploy/kyc-grabber.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now kyc-grabber
sudo systemctl status kyc-grabber
journalctl -u kyc-grabber -f
```

`KYC_MAIL__MODE=imap` and the mailbox credentials go in `/opt/kyc-grabber/.env` (mode `600`, owned by `kyc`).

## Option E — Docker / Compose

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Mount a volume for `data/` and provide secrets via environment variables or a secret file.

---

## Operational notes

* **Health check** — `GET /api/health` (liveness) and `GET /api/status` (watcher/worker/queue depth).
* **Logs** — the process logs to stdout; capture it however your supervisor prefers. Levels are set with
  `KYC_LOG_LEVEL`. On Windows consoles the logger reconfigures stdout to UTF-8 automatically.
* **Graceful shutdown** — `SIGINT`/`SIGTERM` (or stopping the service) stops the mail source, cancels the
  workers and closes the SQLite store. In-flight jobs are marked `failed` with `cancelled during shutdown`
  and can be re-queued from the dashboard.
* **Backups** — back up `data/kyc_grabber.sqlite3` and `data/out/` if the generated workbooks must be retained.
* **Disk growth** — the event log is pruned every 5 minutes (latest 5000 events kept). Workbooks and
  outbox messages accumulate; add a retention policy for `data/out` and `data/outbox`.
* **Security** — the dashboard has no authentication: keep it on localhost or behind SSO/a reverse proxy.
  Never commit `.env`.

## From dev to production

| Step | Change |
| --- | --- |
| 1 | `KYC_MAIL__MODE=imap` + real IMAP settings (app password or OAuth2) |
| 2 | `KYC_OUTBOUND__MODE=smtp` + relay settings |
| 3 | Replace `InternalDatabaseClient` with the real master-data adapter |
| 4 | Replace `ExternalDatabaseClient._simulate` with the real provider call |
| 5 | Tighten `KYC_FILTER__ALLOWED_SENDERS` to the real compliance team addresses |
| 6 | Raise `KYC_EXTERNAL__CONCURRENCY` / `KYC_PIPELINE__WORKERS` to match provider rate limits |
| 7 | Turn on provider result caching / a real rate limiter if the contract requires it |
