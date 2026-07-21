from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


JsonValue = dict[str, Any] | list[Any]


@dataclass(frozen=True)
class ApiError(RuntimeError):
    message: str
    status: int | None = None

    def __str__(self) -> str:
        if self.status is None:
            return self.message
        return f"HTTP {self.status}: {self.message}"


class HttpClient:
    def __init__(self, instance: str, access_token: str | None = None) -> None:
        self.base_url = normalize_instance_url(instance)
        self.access_token = access_token

    def get(self, path: str, params: dict[str, str | int] | None = None) -> JsonValue:
        url = self._url(path, params)
        return self._request("GET", url)

    def post(
        self,
        path: str,
        form: dict[str, str | int | bool] | None = None,
        *,
        access_token: str | None = None,
    ) -> JsonValue:
        token = self.access_token if access_token is None else access_token
        url = self._url(path)
        body = urlencode(form or {}).encode("utf-8")
        return self._request("POST", url, body=body, access_token=token)

    def put(
        self,
        path: str,
        form: dict[str, str | int | bool] | None = None,
    ) -> JsonValue:
        url = self._url(path)
        body = urlencode(form or {}).encode("utf-8")
        return self._request("PUT", url, body=body)

    def _url(self, path: str, params: dict[str, str | int] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        access_token: str | None = None,
    ) -> JsonValue:
        headers = {
            "Accept": "application/json",
            "User-Agent": "btmastodon/0.1",
        }
        token = self.access_token if access_token is None else access_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(_error_message(detail), exc.code) from exc
        except URLError as exc:
            raise ApiError(str(exc.reason)) from exc

        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError("Server returned invalid JSON") from exc


def normalize_instance_url(instance: str) -> str:
    instance = instance.strip().rstrip("/")
    if not instance:
        raise ValueError("Instance cannot be empty")
    if not instance.startswith(("http://", "https://")):
        instance = f"https://{instance}"
    return instance


def _error_message(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload.strip() or "Request failed"
    if isinstance(data, dict):
        return str(data.get("error_description") or data.get("error") or data)
    return str(data)
