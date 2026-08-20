#!/usr/bin/env python3
"""In-memory authenticated HTTPS client for the Engine control API."""

import http.cookiejar
import urllib.error
import urllib.request
from urllib.parse import urlparse

from engine.contracts import strict_json


__all__ = ("AuthenticatedApiClient",)


class AuthenticatedApiClient:
    def __init__(self, base_url):
        self.base_url = str(base_url or "").rstrip("/")
        if urlparse(self.base_url).scheme.lower() != "https":
            raise ValueError("The strategy API requires an https:// URL.")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf_token = ""

    @staticmethod
    def _decode_response(response):
        content = response.read().decode("utf-8")
        return strict_json.loads(content) if content else {}

    def _request(self, method, url, payload=None, timeout=120, include_csrf=False):
        headers = {
            "Accept": "application/json",
            "User-Agent": "TradeEngineCLI/1.0 (+https://trade.duckduckrun.com)",
        }
        body = None
        if payload is not None:
            body = strict_json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if include_csrf:
            if not self.csrf_token:
                raise RuntimeError("Authenticated API session has no CSRF token.")
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, self._decode_response(response)
        except urllib.error.HTTPError as exc:
            return exc.code, self._decode_response(exc)

    def login(self, email, password):
        status, response = self._request(
            "POST",
            self.base_url + "/auth/login",
            {"email": str(email or "").strip(), "password": password},
            timeout=30,
        )
        if status != 200 or not response.get("authenticated"):
            raise PermissionError(response.get("error") or f"Authentication failed ({status}).")
        self.csrf_token = str(response.get("csrfToken") or "")
        if not self.csrf_token:
            raise PermissionError("Authentication response did not contain a CSRF token.")
        return response.get("user") or {}

    def get(self, url, timeout=30):
        return self._request("GET", url, timeout=timeout)

    def post(self, url, payload, timeout=120):
        return self._request("POST", url, payload, timeout=timeout, include_csrf=True)

    def delete(self, url, timeout=30):
        return self._request("DELETE", url, timeout=timeout, include_csrf=True)

    def logout(self):
        if not self.csrf_token:
            return
        try:
            self._request(
                "POST",
                self.base_url + "/auth/logout",
                {},
                timeout=10,
                include_csrf=True,
            )
        except Exception:
            pass
        finally:
            self.csrf_token = ""
            self.cookies.clear()
