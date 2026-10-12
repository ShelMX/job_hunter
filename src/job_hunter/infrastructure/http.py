"""HTTP-клиент с ретраями и вежливым поведением (httpx + tenacity)."""

from collections.abc import Callable
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class RetryableHTTPError(Exception):
    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.status_code = status_code


class HttpClient:
    """Тонкая обёртка над httpx.Client: JSON GET/POST с ретраями на 429/5xx."""

    def __init__(self, *, user_agent: str, timeout: float = 20.0, max_retries: int = 3) -> None:
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self._request_with_retry: Callable[..., httpx.Response] = retry(
            retry=retry_if_exception_type((httpx.TransportError, RetryableHTTPError)),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
            stop=stop_after_attempt(max_retries),
            reraise=True,
        )(self._do_request)

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Any:  # noqa: ANN401 — JSON непредсказуем по форме
        kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = self._request_with_retry("GET", url, **kwargs)
        return response.json()

    def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> str:
        """Для не-JSON источников (RSS/XML и т.п.)."""
        kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = self._request_with_retry("GET", url, **kwargs)
        return response.text

    def post_form(self, url: str, *, data: dict[str, str], headers: dict[str, str] | None = None) -> Any:  # noqa: ANN401
        response = self._request_with_retry("POST", url, data=data, headers=headers)
        return response.json()

    def post_json(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:  # noqa: ANN401
        kwargs: dict[str, Any] = {"json": json, "headers": headers}
        if timeout is not None:
            kwargs["timeout"] = timeout
        response = self._request_with_retry("POST", url, **kwargs)
        return response.json()

    def _do_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, url, **kwargs)
        if response.status_code in _RETRYABLE_STATUSES:
            raise RetryableHTTPError(response.status_code, url)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
