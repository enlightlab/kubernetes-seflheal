"""Proxy error pages — FastAPI staging should return 502/503 HTML, not Python tracebacks."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

_WEB_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _WEB_ROOT not in sys.path:
    sys.path.insert(0, _WEB_ROOT)

from server import _proxy_error_response, _proxy_impl  # noqa: E402


class TestProxyErrors(unittest.TestCase):
    def _request(self, accept: str = "text/html") -> MagicMock:
        req = MagicMock()
        req.method = "GET"
        req.url.query = ""
        req.headers.get.return_value = accept
        return req

    def test_connection_refused_returns_502_html(self) -> None:
        err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch("server.urllib.request.urlopen", side_effect=err):
            resp = _proxy_impl(
                "http://fastapi.enlight-staging.svc.cluster.local",
                "",
                self._request(),
                "unavailable",
                app_label="FastAPI staging app",
            )
        self.assertEqual(resp.status_code, 502)
        self.assertIn(b"502 Bad Gateway", resp.body)
        self.assertIn(b"FastAPI staging app", resp.body)
        self.assertNotIn(b"urlopen error", resp.body)

    def test_health_path_returns_json_when_down(self) -> None:
        err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with patch("server.urllib.request.urlopen", side_effect=err):
            resp = _proxy_impl(
                "http://fastapi.enlight-staging.svc.cluster.local",
                "health",
                self._request(accept="application/json"),
                "unavailable",
                app_label="FastAPI staging app",
            )
        self.assertEqual(resp.status_code, 502)
        self.assertIn(b'"status":"unavailable"', resp.body)

    def test_timeout_returns_503(self) -> None:
        resp = _proxy_error_response(
            self._request(), "", status=503, title="503 Service Unavailable",
            detail="Timed out.",
        )
        self.assertEqual(resp.status_code, 503)
        self.assertIn(b"503 Service Unavailable", resp.body)


if __name__ == "__main__":
    unittest.main()
