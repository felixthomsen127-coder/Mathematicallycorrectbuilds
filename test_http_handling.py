import time
import json
import requests
from types import SimpleNamespace

import pytest

from Mathematically_correct_builds import data_sources


class DummyResponse(requests.Response):
    def __init__(self, status=200, content=b"", headers=None, url="http://example/"):
        super().__init__()
        self.status_code = status
        self._content = content
        self.headers = headers or {}
        self.url = url


def test_safe_json_raises_on_invalid():
    res = DummyResponse(status=200, content=b"not json")
    with pytest.raises(data_sources.DataSourceError):
        data_sources._safe_json(res)


def test_http_get_sets_user_agent(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None, **kwargs):
        captured['headers'] = headers
        return DummyResponse(status=200, content=b'{"ok":1}')

    monkeypatch.setattr(data_sources, '_HTTP_SESSION', SimpleNamespace(get=fake_get))
    res = data_sources._http_get('http://example/')
    assert 'User-Agent' in captured['headers']


def test_http_get_retries_on_429(monkeypatch):
    calls = {'n': 0}

    def fake_get(url, headers=None, timeout=None, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            return DummyResponse(status=429, content=b'', headers={'Retry-After': '0.05'})
        return DummyResponse(status=200, content=b'{"ok":true}')

    monkeypatch.setattr(data_sources, '_HTTP_SESSION', SimpleNamespace(get=fake_get))
    # should not raise
    res = data_sources._http_get('http://example/', timeout=1.0)
    assert res.status_code == 200
