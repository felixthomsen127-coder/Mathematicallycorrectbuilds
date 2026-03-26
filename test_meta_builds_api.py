from __future__ import annotations

import main


def test_meta_builds_updater_health_endpoint_reports_latest_state(monkeypatch):
    app = main.app
    app.config["TESTING"] = True

    fake_report = {
        "status": "ok",
        "updated": 3,
        "failed": 1,
        "total_targets": 4,
        "failed_targets": ["aatrox/top"],
    }

    monkeypatch.setattr(main, "get_last_meta_build_update_report", lambda: fake_report)

    with main._meta_build_refresh_lock:
        main._meta_build_refresh_latest_job_id = "job-1"
        main._meta_build_refresh_jobs["job-1"] = {
            "job_id": "job-1",
            "status": "complete",
            "error": "",
        }

    with app.test_client() as client:
        resp = client.get("/api/meta-builds/updater-health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["latest_job_id"] == "job-1"
    assert data["latest_job"]["status"] == "complete"
    assert data["last_run_report"]["updated"] == 3
    assert data["last_run_report"]["failed"] == 1
