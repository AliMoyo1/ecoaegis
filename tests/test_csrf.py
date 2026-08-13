"""CSRF middleware tests (audit fix: validate_csrf was dead code)."""
from __future__ import annotations

import http.cookiejar
import urllib.request
import urllib.parse


def _login(opener, email="officer@she.local", password="ChangeMe!123"):
    """Login via the real form; returns the CSRF cookie value."""
    data = urllib.parse.urlencode({"email": email, "password": password}).encode()
    req = urllib.request.Request("http://127.0.0.1:8082/login", data=data)
    try:
        opener.open(req)
    except urllib.error.HTTPError:
        pass  # 303 redirect is fine
    for h in opener.handlers:
        if isinstance(h, urllib.request.HTTPCookieProcessor):
            for c in h.cookiejar:
                if c.name == "she_csrf":
                    return c.value
    return None


def _post(opener, url, data: dict | None = None, headers: dict | None = None):
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        resp = opener.open(req)
        return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class TestCSRFMiddleware:
    def test_post_without_token_blocked(self):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        _login(opener)
        # POST without X-CSRF-Token -> 403
        status = _post(opener, "http://127.0.0.1:8082/incidents/api/create",
                       {"title": "x", "severity": "low", "incident_type": "accident",
                        "occurred_at": "2026-08-13T10:00:00"})
        assert status == 403, f"expected 403, got {status}"

    def test_post_with_token_allowed(self):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        token = _login(opener)
        assert token, "CSRF cookie should be set after login"
        status = _post(opener, "http://127.0.0.1:8082/incidents/api/create",
                       {"title": "CSRF test incident", "description": "test",
                        "severity": "low",
                        "incident_type": "accident", "occurred_at": "2026-08-13T10:00:00"},
                       {"X-CSRF-Token": token})
        assert status in (200, 201), f"expected 200/201, got {status}"

    def test_form_field_token_allowed(self):
        """Server-rendered forms (logout) send csrf_token as a form field."""
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        token = _login(opener)
        status = _post(opener, "http://127.0.0.1:8082/logout",
                       {"csrf_token": token})
        assert status in (200, 303), f"expected 200/303, got {status}"

    def test_wrong_token_blocked(self):
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        _login(opener)
        status = _post(opener, "http://127.0.0.1:8082/incidents/api/create",
                       {"title": "x", "severity": "low", "incident_type": "accident",
                        "occurred_at": "2026-08-13T10:00:00"},
                       {"X-CSRF-Token": "wrong-token-value"})
        assert status == 403
