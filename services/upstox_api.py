# services/upstox_api.py
"""
Thin client for Upstox's TOTP-login and profile endpoints.

DuoGuard uses this in `UPSTOX_DIRECT_LOGIN=true` mode so it can
authenticate against Upstox without relying on the separate dashboard
project being up and running.

Reference endpoints:
    POST https://api.upstox.com/v2/totp-login/authorization/token
         headers: x-api-key, Content-Type: application/json
         body:    {"totp": "123456", "pin": "<pin>"}
         resp:    {"access_token": "...", ...}

    GET  https://api.upstox.com/v2/user/profile
         headers: Authorization: Bearer <access_token>
"""
from __future__ import annotations

import requests


class UpstoxError(Exception):
    """Raised for any non-200 response or transport error from Upstox."""


class UpstoxClient:
    def __init__(
        self,
        api_key: str,
        pin: str,
        *,
        totp_url: str,
        profile_url: str,
        timeout: int = 30,
    ):
        if not api_key:
            raise UpstoxError("UPSTOX_API_KEY is not configured")
        if not pin:
            raise UpstoxError("UPSTOX_PIN is not configured")
        self.api_key = api_key
        self.pin = pin
        self.totp_url = totp_url
        self.profile_url = profile_url
        self.timeout = timeout

    # ------------------------------------------------------------------ login

    def login_with_totp(self, totp: str) -> dict:
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }
        payload = {"totp": totp, "pin": self.pin}
        try:
            r = requests.post(
                self.totp_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise UpstoxError(
                f"Network error contacting Upstox: {exc}"
            ) from exc

        if r.status_code != 200:
            raise UpstoxError(self._format_error(r))
        return r.json()

    # ---------------------------------------------------------------- profile

    def get_profile(self, access_token: str) -> dict:
        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        try:
            r = requests.get(
                self.profile_url, headers=headers, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise UpstoxError(
                f"Network error contacting Upstox: {exc}"
            ) from exc

        if r.status_code != 200:
            raise UpstoxError(self._format_error(r))
        return r.json()

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _format_error(response: requests.Response) -> str:
        try:
            body = response.json()
            errors = body.get("errors") or []
            if errors and isinstance(errors, list):
                first = errors[0]
                code = (
                    first.get("errorCode")
                    or first.get("error_code")
                    or "?"
                )
                msg = first.get("message") or response.text
                return f"HTTP {response.status_code} [{code}]: {msg}"
            return f"HTTP {response.status_code}: {response.text}"
        except ValueError:
            return f"HTTP {response.status_code}: {response.text}"