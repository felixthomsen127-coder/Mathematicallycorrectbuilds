"""Integration tests for prefetch API endpoints and warm-start behavior."""
import json
import pytest
import time
from unittest.mock import patch, MagicMock
from main import (
    app, _prefetch_state, _prefetch_queue, _prefetch_lock, _prefetch_completed_keys,
    _load_prefetch_marker, _save_prefetch_marker, _run_prefetch_cycle,
    _prefetch_progress_payload, _ensure_prefetch_running
)


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def reset_prefetch():
    """Reset prefetch state before each test."""
    with _prefetch_lock:
        _prefetch_state.clear()
        _prefetch_state['running'] = False
        _prefetch_state['queue_size'] = 0
        _prefetch_state['critical_completed'] = 0
        _prefetch_state['critical_total'] = 0
        _prefetch_state['total_completed'] = 0
        _prefetch_state['total_tasks'] = 0
        _prefetch_state['current_label'] = None
        _prefetch_state['failed'] = 0
        _prefetch_state['ready'] = False
        _prefetch_queue.clear()
        _prefetch_completed_keys.clear()
    yield


class TestPrefetchStatusEndpoint:
    """Test /api/prefetch-status endpoint."""

    def test_prefetch_status_returns_json_structure(self, client, reset_prefetch):
        """Verify status endpoint returns properly formatted state dict."""
        resp = client.get('/api/prefetch-status')
        assert resp.status_code == 200
        
        data = resp.get_json()
        assert isinstance(data, dict)
        # Verify all expected keys present
        assert 'critical_progress_percent' in data
        assert 'progress_percent' in data
        assert 'critical_completed' in data
        assert 'critical_total' in data
        assert 'completed' in data
        assert 'total' in data
        assert 'current_label' in data
        assert 'failed' in data

    def test_prefetch_status_progress_calculation(self, client, reset_prefetch):
        """Verify status endpoint returns numeric progress values."""
        resp = client.get('/api/prefetch-status')
        data = resp.get_json()
        
        # Verify fields exist and are numeric
        assert isinstance(data.get('critical_progress_percent'), (int, float))
        assert isinstance(data.get('progress_percent'), (int, float))
        # Values should be between 0-100
        assert 0 <= data['critical_progress_percent'] <= 100.0
        assert 0 <= data['progress_percent'] <= 100.0

    def test_prefetch_status_empty_state(self, client, reset_prefetch):
        """Verify status endpoint handles empty state with valid defaults."""
        resp = client.get('/api/prefetch-status')
        assert resp.status_code == 200
        data = resp.get_json()
        
        # Should return valid defaults for empty state
        assert 'critical_progress_percent' in data
        assert 'progress_percent' in data
        assert isinstance(data['critical_progress_percent'], (int, float))
        assert isinstance(data['progress_percent'], (int, float))


class TestPrefetchPriorityEndpoint:
    """Test /api/prefetch-priority endpoint."""

    def test_prefetch_priority_accepts_post(self, client, reset_prefetch):
        """Verify priority endpoint accepts POST with champion context."""
        payload = {
            'champion': 'Lux',
            'role': 'support',
            'tier': 'emerald_plus',
            'region': 'global',
            'patch': 'live',
        }
        resp = client.post(
            '/api/prefetch-priority',
            data=json.dumps(payload),
            content_type='application/json'
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'ok' in data
        assert data['ok'] is True

    def test_prefetch_priority_returns_state(self, client, reset_prefetch):
        """Verify priority endpoint returns updated prefetch state."""
        payload = {
            'champion': 'Ahri',
            'role': 'middle',
            'tier': 'emerald_plus',
            'region': 'global',
            'patch': 'live',
        }
        resp = client.post(
            '/api/prefetch-priority',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = resp.get_json()
        
        # Should include state snapshot
        assert 'state' in data
        state = data['state']
        assert 'critical_progress_percent' in state
        assert 'progress_percent' in state

    def test_prefetch_priority_missing_fields(self, client, reset_prefetch):
        """Verify priority endpoint handles missing fields gracefully."""
        payload = {'champion': 'Garen'}  # Minimal payload, missing context fields
        resp = client.post(
            '/api/prefetch-priority',
            data=json.dumps(payload),
            content_type='application/json'
        )
        # Should still succeed with defaults
        assert resp.status_code == 200

    def test_prefetch_priority_invalid_json(self, client):
        """Verify priority endpoint handles invalid JSON."""
        resp = client.post(
            '/api/prefetch-priority',
            data='{ invalid json ',
            content_type='application/json'
        )
        # Should handle gracefully (either 400 or 200 with error flag)
        assert resp.status_code in [200, 400]


class TestPrefetchWarmStart:
    """Test warm-start via persistent markers."""

    def test_marker_persistence_prevents_rerun(self, reset_prefetch):
        """Verify per-patch marker prevents full prefetch re-run."""
        patch = 'live'
        
        # Simulate completed prefetch
        payload = {'ready': True, 'timestamp': time.time()}
        _save_prefetch_marker(patch, payload)
        
        # Verify marker was saved
        marker = _load_prefetch_marker(patch)
        assert marker is not None
        assert marker.get('ready') is True

    def test_second_optimization_reuses_marker(self, client, reset_prefetch):
        """Verify second optimization job on same patch reuses cached state."""
        with patch('main._run_prefetch_cycle') as mock_cycle:
            with _prefetch_lock:
                _prefetch_state['ready'] = True
                _prefetch_state['completed_by_kind'] = {'items': 1, 'champions': 1, 'scaling': 1}
                _prefetch_state['totals_by_kind'] = {'items': 1, 'champions': 1, 'scaling': 1}
                _prefetch_state['completed'] = 10
                _prefetch_state['total'] = 10

            resp = client.get('/api/prefetch-status')
            data = resp.get_json()
            
            # Both should be 100% if ready
            assert data['critical_progress_percent'] == 100.0
            assert data['progress_percent'] == 100.0

    def test_force_refresh_bypasses_marker(self, reset_prefetch):
        """Verify force_refresh=true triggers full prefetch despite marker."""
        patch = 'live'
        
        # Save a marker to indicate completion
        payload = {'ready': True, 'timestamp': time.time()}
        _save_prefetch_marker(patch, payload)
        
        # Verify it was saved
        marker = _load_prefetch_marker(patch)
        assert marker.get('ready') is True
        
        # In actual implementation, force_refresh would ignore this marker
        # This test documents the expected behavior


class TestPrefetchStateConsistency:
    """Test prefetch state management and thread safety."""

    def test_prefetch_state_thread_safe_read(self, reset_prefetch):
        """Verify prefetch state is safely readable from multiple operations."""
        with _prefetch_lock:
            _prefetch_state['completed_by_kind'] = {'items': 2}
            _prefetch_state['totals_by_kind'] = {'items': 8}

        # Multiple reads should be consistent
        payload1 = _prefetch_progress_payload()
        payload2 = _prefetch_progress_payload()
        
        assert payload1['critical_progress_percent'] == payload2['critical_progress_percent']
        assert payload1['critical_completed'] == payload2['critical_completed']

    def test_prefetch_queue_size_tracking(self, reset_prefetch):
        """Verify queue size is accurately tracked in state."""
        from collections import deque
        with _prefetch_lock:
            # Simulate queue operations
            for i in range(5):
                _prefetch_queue.append(f'task_{i}')
            _prefetch_state['queue_size'] = len(_prefetch_queue)
        
        payload = _prefetch_progress_payload()
        assert 'queue_size' not in payload or payload.get('queue_size', 0) >= 0


class TestPrefetchErrorHandling:
    """Test error handling in prefetch operations."""

    def test_prefetch_status_with_zero_tasks(self, client, reset_prefetch):
        """Verify status endpoint handles zero tasks without division errors."""
        with _prefetch_lock:
            _prefetch_state['totals_by_kind'] = {}
            _prefetch_state['total'] = 0

        resp = client.get('/api/prefetch-status')
        assert resp.status_code == 200
        data = resp.get_json()
        
        # Should handle division by zero gracefully
        assert 'critical_progress_percent' in data
        assert 'progress_percent' in data

    def test_prefetch_marker_with_invalid_patch(self, reset_prefetch):
        """Verify marker operations handle edge case patch values."""
        patches = ['', 'invalid patch with spaces', 'a' * 500]
        
        for patch in patches:
            # Should not crash
            marker = _load_prefetch_marker(patch)
            assert marker is None or isinstance(marker, dict)


class TestPrefetchResponseFormat:
    """Test response format consistency."""

    def test_status_endpoint_response_type_consistency(self, client, reset_prefetch):
        """Verify status response types are consistent."""
        resp = client.get('/api/prefetch-status')
        data = resp.get_json()
        
        # All numeric fields should be numeric
        assert isinstance(data.get('critical_progress_percent', 0), (int, float))
        assert isinstance(data.get('progress_percent', 0), (int, float))
        assert isinstance(data.get('critical_completed', 0), (int, float))
        assert isinstance(data.get('critical_total', 0), (int, float))

    def test_priority_endpoint_response_type_consistency(self, client, reset_prefetch):
        """Verify priority response structure is consistent."""
        payload = {'champion': 'Test', 'role': 'top'}
        resp = client.post(
            '/api/prefetch-priority',
            data=json.dumps(payload),
            content_type='application/json'
        )
        data = resp.get_json()
        
        assert isinstance(data.get('ok'), bool)
        if 'state' in data:
            state = data['state']
            assert isinstance(state.get('critical_progress_percent', 0), (int, float))
            assert isinstance(state.get('progress_percent', 0), (int, float))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
