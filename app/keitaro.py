import os
from collections.abc import Iterator

import httpx
from fastapi import HTTPException


class Tracker:
    def __init__(self, http: httpx.Client):
        self.http = http

    def call(self, method: str, path: str, **kwargs):
        try:
            response = self.http.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise HTTPException(
                404 if status == 404 else 502,
                f"Keitaro: {method} {path} вернул HTTP {status}.",
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise HTTPException(502, f"Не удалось получить ответ Keitaro: {method} {path}.") from exc

    def get(self, path: str):
        return self.call("GET", path)

    def list(self, path: str):
        data = self.get(path)
        if not isinstance(data, list):
            raise HTTPException(502, "Keitaro вернул неожиданный формат списка.")
        return data


def tracker() -> Iterator[Tracker]:
    base = os.getenv("KEITARO_URL", "").strip().rstrip("/")
    key = os.getenv("KEITARO_API_KEY", "").strip()
    if not base.startswith(("https://", "http://")) or not key:
        raise HTTPException(503, "Укажите KEITARO_URL и KEITARO_API_KEY в .env.")
    if not base.endswith("/admin_api/v1"):
        base += "/admin_api/v1"
    with httpx.Client(base_url=base, headers={"Api-Key": key}, timeout=15) as http:
        yield Tracker(http)
