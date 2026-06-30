from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .config import Settings


class KisApiError(RuntimeError):
    pass


class KisClient:
    def __init__(self, settings: Settings, token_cache_path: str | Path = "data/kis_token.json"):
        self.settings = settings
        self.token_cache_path = Path(token_cache_path)
        self._last_request_at = 0.0

    def get(self, path: str, tr_id: str, params: dict[str, Any], tr_cont: str = "") -> dict[str, Any]:
        query = parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.settings.base_url}{path}?{query}"
        return self._send("GET", url, tr_id=tr_id, tr_cont=tr_cont)

    def post(self, path: str, tr_id: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.base_url}{path}"
        return self._send("POST", url, tr_id=tr_id, body=body)

    def token(self) -> str:
        cached = self._read_token_cache()
        now = int(time.time())
        if cached and cached.get("access_token") and int(cached.get("expires_at", 0)) > now + 60:
            return str(cached["access_token"])

        if not self.settings.app_key or not self.settings.app_secret:
            raise KisApiError("KIS_APP_KEY and KIS_APP_SECRET are required. Put them in .env.")

        url = f"{self.settings.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        req = request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json; charset=utf-8"},
            method="POST",
        )
        payload = self._open_json_with_retries(req)
        access_token = payload.get("access_token")
        if not access_token:
            raise KisApiError(f"Token response did not include access_token: {payload}")

        expires_in = int(payload.get("expires_in", 86400))
        self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_cache_path.write_text(
            json.dumps(
                {
                    "access_token": access_token,
                    "expires_at": now + expires_in,
                    "env": self.settings.env,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return str(access_token)

    def _send(
        self,
        method: str,
        url: str,
        tr_id: str,
        body: dict[str, Any] | None = None,
        tr_cont: str = "",
    ) -> dict[str, Any]:
        for attempt in range(6):
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self.token()}",
                "appkey": self.settings.app_key,
                "appsecret": self.settings.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            if tr_cont:
                headers["tr_cont"] = tr_cont

            data = json.dumps(body).encode("utf-8") if body is not None else None
            req = request.Request(url, data=data, headers=headers, method=method)
            self._throttle()
            try:
                payload = self._open_json(req, timeout=self.settings.kis_request_timeout_seconds)
            except KisApiError as exc:
                if self._is_retryable_error(exc) and attempt < 5:
                    self._sleep_before_retry(attempt)
                    continue
                raise
            rt_cd = payload.get("rt_cd")
            if rt_cd in (None, "0"):
                return payload
            msg_cd = payload.get("msg_cd", "")
            msg = payload.get("msg1", "")
            if msg_cd in {"EGW00201", "EGW00215"} and attempt < 5:
                self._sleep_before_retry(attempt)
                continue
            raise KisApiError(f"KIS API error {msg_cd}: {msg}")
        raise KisApiError("KIS API request failed after retries")

    def _open_json_with_retries(self, req: request.Request) -> dict[str, Any]:
        for attempt in range(4):
            try:
                self._throttle()
                return self._open_json(req, timeout=self.settings.kis_request_timeout_seconds)
            except KisApiError as exc:
                if self._is_retryable_error(exc) and attempt < 3:
                    self._sleep_before_retry(attempt)
                    continue
                raise
        raise KisApiError("KIS API request failed after retries")

    def _sleep_before_retry(self, attempt: int) -> None:
        time.sleep(max(2.0, self.settings.kis_min_interval_seconds * (attempt + 2), 2.0 * (attempt + 1)))

    @staticmethod
    def _is_retryable_error(exc: KisApiError) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "network error",
                "timed out",
                "timeout",
                "temporarily unavailable",
                "connection reset",
                "http 429",
                "http 5",
            )
        )

    def _throttle(self) -> None:
        min_interval = max(float(self.settings.kis_min_interval_seconds or 0), 0)
        if min_interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _read_token_cache(self) -> dict[str, Any] | None:
        if not self.token_cache_path.exists():
            return None
        try:
            cached = json.loads(self.token_cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if cached.get("env") != self.settings.env:
            return None
        return cached

    @staticmethod
    def _open_json(req: request.Request, timeout: float = 20) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=timeout) as res:
                raw = res.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                return json.loads(detail)
            except json.JSONDecodeError:
                raise KisApiError(f"HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise KisApiError(f"Network error: {exc}") from exc
        except TimeoutError as exc:
            raise KisApiError(f"Network error: timed out ({exc})") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KisApiError(f"Non-JSON response: {raw[:500]}") from exc
