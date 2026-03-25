import sys

import main


def test_balanced_worker_count_uses_physical_core_hint(monkeypatch):
    class _FakePsutil:
        @staticmethod
        def cpu_count(logical=False):
            return 6 if logical is False else 12

    monkeypatch.setattr(main.os, "cpu_count", lambda: 12)
    monkeypatch.setitem(sys.modules, "psutil", _FakePsutil)

    assert main._balanced_worker_count() == 4


def test_prefetch_progress_payload_reports_critical_progress():
    original_state = dict(main._prefetch_state)
    original_queue = list(main._prefetch_queue)
    try:
        with main._prefetch_lock:
            main._prefetch_queue.clear()
            main._prefetch_state.update(
                {
                    "running": True,
                    "ready": False,
                    "total": 10,
                    "completed": 5,
                    "failed": 1,
                    "totals_by_kind": {"items": 1, "champions": 1, "scaling": 4, "meta": 4},
                    "completed_by_kind": {"items": 1, "champions": 1, "scaling": 2, "meta": 1},
                    "current_label": "Scaling: Aatrox",
                }
            )

        payload = main._prefetch_progress_payload()

        assert payload["progress_percent"] == 50.0
        assert payload["critical_total"] == 6
        assert payload["critical_completed"] == 4
        assert payload["critical_progress_percent"] == 66.7
    finally:
        with main._prefetch_lock:
            main._prefetch_queue.clear()
            main._prefetch_queue.extend(original_queue)
            main._prefetch_state.clear()
            main._prefetch_state.update(original_state)


def test_prioritize_prefetch_for_champion_adds_front_loaded_tasks(monkeypatch):
    original_state = dict(main._prefetch_state)
    original_queue = list(main._prefetch_queue)
    original_pending = set(main._prefetch_pending_keys)
    original_completed = set(main._prefetch_completed_keys)
    try:
        monkeypatch.setattr(main, "_ensure_prefetch_running", lambda **kwargs: None)
        monkeypatch.setattr(main.riot, "get_latest_patch", lambda force_refresh=False: "patch-1")

        with main._prefetch_lock:
            main._prefetch_queue.clear()
            main._prefetch_pending_keys.clear()
            main._prefetch_completed_keys.clear()
            main._prefetch_state.update({"priority_champion": "", "queue_size": 0})

        main._prioritize_prefetch_for_champion(
            champion="Lux",
            role="middle",
            tier="diamond_plus",
            region="euw",
            patch="patch-1",
        )

        with main._prefetch_lock:
            queued = list(main._prefetch_queue)
            assert main._prefetch_state["priority_champion"] == "Lux"

        assert len(queued) == 2
        assert queued[0]["kind"] == "scaling"
        assert queued[0]["champion"] == "Lux"
        assert queued[1]["kind"] == "meta"
        assert queued[1]["role"] == "middle"
        assert queued[1]["tier"] == "diamond_plus"
        assert queued[1]["region"] == "euw"
    finally:
        with main._prefetch_lock:
            main._prefetch_queue.clear()
            main._prefetch_queue.extend(original_queue)
            main._prefetch_pending_keys.clear()
            main._prefetch_pending_keys.update(original_pending)
            main._prefetch_completed_keys.clear()
            main._prefetch_completed_keys.update(original_completed)
            main._prefetch_state.clear()
            main._prefetch_state.update(original_state)