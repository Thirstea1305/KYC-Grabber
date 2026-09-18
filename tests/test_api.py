"""API-level tests for the dashboard endpoints."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from kyc_grabber.runtime import build_context
from kyc_grabber.web.app import create_app
from tests.helpers import make_mail, make_settings


def build_client(tmp_path: Path) -> tuple[TestClient, object]:
    context = build_context(make_settings(tmp_path))
    return TestClient(create_app(context)), context


def test_health_and_status(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        status = client.get("/api/status").json()
        assert status["source"]["name"] == "filesystem"
        assert status["worker"]["workers"] == 1
        assert status["config"]["mail_mode"] == "filesystem"
        assert status["data"]["internal_records"] == 3
    finally:
        context.close()


def test_dashboard_html_is_served(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        page = client.get("/")
        assert page.status_code == 200
        assert "KYC Grabber" in page.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/../app.py").status_code in {404, 403}
    finally:
        context.close()


def test_sample_csv_endpoint(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        response = client.get("/api/sample/codes.csv")
        assert response.status_code == 200
        assert "third_party_code" in response.text
    finally:
        context.close()


def test_simulate_email_json_drops_a_file_into_the_inbox(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        response = client.post("/api/simulate/email", json={
            "sender": "analyst@corp.com",
            "subject": "[KYC] test",
            "filename": "codes.csv",
            "csv_text": "third_party_code\nTP-0001\n",
        })
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["accepted"] is True
        assert payload["route"] == "filesystem-inbox"
        assert len(list(context.settings.inbox_path.glob("*.eml"))) == 1
    finally:
        context.close()


def test_simulate_email_upload_accepts_a_multipart_file(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        response = client.post(
            "/api/simulate/email/upload",
            data={"sender": "analyst@corp.com", "subject": "[KYC] upload"},
            files={"file": ("codes.csv", b"third_party_code\nTP-0002\n", "text/csv")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] is True
    finally:
        context.close()


def test_jobs_listing_detail_and_workbook_download(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        assert client.get("/api/jobs").json() == {"jobs": [], "counts": {}}

        async def run_job() -> str:
            job_id = await context.pipeline.ingest(make_mail(
                csv_text="third_party_code\nTP-0001\n", message_id="<api@corp.com>"
            ))
            await context.pipeline.process_job(job_id)
            return job_id

        job_id = asyncio.run(run_job())

        listing = client.get("/api/jobs").json()
        assert listing["counts"]["completed"] == 1
        assert listing["jobs"][0]["id"] == job_id

        detail = client.get(f"/api/jobs/{job_id}").json()
        assert detail["items"][0]["code"] == "TP-0001"
        assert detail["progress"] == 100.0

        download = client.get(f"/api/jobs/{job_id}/workbook")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert download.content[:2] == b"PK"  # xlsx is a zip container

        events = client.get("/api/events", params={"job_id": job_id}).json()["events"]
        assert any("completed" in event["message"] for event in events)
    finally:
        context.close()


def test_unknown_job_returns_404(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        assert client.get("/api/jobs/NOPE").status_code == 404
        assert client.get("/api/jobs/NOPE/workbook").status_code == 404
        assert client.post("/api/jobs/NOPE/rerun").status_code == 404
    finally:
        context.close()


def test_rejected_job_cannot_be_rerun(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        async def reject() -> str:
            await context.pipeline.ingest(make_mail(filename="notes.txt", message_id="<rej@corp.com>"))
            return context.store.list_jobs(limit=1)[0]["id"]

        job_id = asyncio.run(reject())
        assert client.post(f"/api/jobs/{job_id}/rerun").status_code == 400
    finally:
        context.close()


def test_clear_external_cache(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        async def warm() -> None:
            await context.external.fetch("TP-0001")

        asyncio.run(warm())
        assert client.post("/api/external/cache/clear").json()["cleared"] == 1
        assert client.get("/api/status").json()["data"]["external"]["cached_codes"] == 0
    finally:
        context.close()


def test_inbox_state_endpoint(tmp_path: Path) -> None:
    client, context = build_client(tmp_path)
    try:
        payload = client.get("/api/inbox").json()
        assert payload["pending"] == []
        context.settings.inbox_path.mkdir(parents=True, exist_ok=True)
        (context.settings.inbox_path / "pending.eml").write_bytes(b"From: a@b.com\r\n\r\nhi")
        assert client.get("/api/inbox").json()["pending"] == ["pending.eml"]
    finally:
        context.close()
