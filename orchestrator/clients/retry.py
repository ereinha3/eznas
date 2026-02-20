"""Retry utilities for service API calls.

Provides exponential backoff retry logic for transient HTTP failures.
Only retries on connection errors and 5xx server errors — 4xx client
errors (auth failures, validation, not found) are never retried.
"""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

import httpx

log = logging.getLogger(__name__)

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0  # seconds
DEFAULT_BACKOFF_MAX = 30.0  # cap on backoff time

# Exceptions that trigger a retry
RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)

# HTTP status codes that trigger a retry (server-side errors)
RETRYABLE_STATUS_CODES = {500, 502, 503, 504, 520, 521, 522, 523, 524}

T = TypeVar("T")


def retry_on_failure(
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    retryable_exceptions: Tuple[Type[Exception], ...] = RETRYABLE_EXCEPTIONS,
) -> Callable:
    """Decorator that retries a function on transient HTTP failures.

    Usage::

        @retry_on_failure(max_retries=3)
        def fetch_data(client, url):
            return client.get(url)

    Exponential backoff: attempt 1 waits backoff_base seconds,
    attempt 2 waits 2×backoff_base, attempt 3 waits 4×backoff_base, etc.
    Capped at backoff_max seconds.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)

                    # If the result is an httpx.Response, check for retryable status
                    if isinstance(result, httpx.Response):
                        if result.status_code in RETRYABLE_STATUS_CODES:
                            if attempt < max_retries:
                                delay = _compute_delay(attempt, backoff_base, backoff_max)
                                log.debug(
                                    "Retrying %s (HTTP %s, attempt %d/%d, backoff %.1fs)",
                                    func.__name__,
                                    result.status_code,
                                    attempt + 1,
                                    max_retries,
                                    delay,
                                )
                                time.sleep(delay)
                                continue

                    return result

                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt < max_retries:
                        delay = _compute_delay(attempt, backoff_base, backoff_max)
                        log.debug(
                            "Retrying %s (%s, attempt %d/%d, backoff %.1fs)",
                            func.__name__,
                            exc.__class__.__name__,
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        raise

                except httpx.HTTPStatusError as exc:
                    # Only retry server errors, not client errors
                    if exc.response.status_code in RETRYABLE_STATUS_CODES:
                        last_exception = exc
                        if attempt < max_retries:
                            delay = _compute_delay(attempt, backoff_base, backoff_max)
                            log.debug(
                                "Retrying %s (HTTP %s, attempt %d/%d, backoff %.1fs)",
                                func.__name__,
                                exc.response.status_code,
                                attempt + 1,
                                max_retries,
                                delay,
                            )
                            time.sleep(delay)
                        else:
                            raise
                    else:
                        # 4xx errors are not retried
                        raise

            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            raise RuntimeError(f"Retry logic exhausted for {func.__name__}")

        return wrapper

    return decorator


def retry_request(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_max: float = DEFAULT_BACKOFF_MAX,
    **kwargs: Any,
) -> T:
    """Call a function with retry logic (non-decorator form).

    Usage::

        response = retry_request(client.get, "/api/v3/rootfolder")
        response = retry_request(client.post, "/api/v3/downloadclient", json=payload)
    """
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            result = func(*args, **kwargs)

            if isinstance(result, httpx.Response):
                if result.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < max_retries:
                        delay = _compute_delay(attempt, backoff_base, backoff_max)
                        log.debug(
                            "Retrying request (HTTP %s, attempt %d/%d, backoff %.1fs)",
                            result.status_code,
                            attempt + 1,
                            max_retries,
                            delay,
                        )
                        time.sleep(delay)
                        continue

            return result

        except RETRYABLE_EXCEPTIONS as exc:
            last_exception = exc
            if attempt < max_retries:
                delay = _compute_delay(attempt, backoff_base, backoff_max)
                log.debug(
                    "Retrying request (%s, attempt %d/%d, backoff %.1fs)",
                    exc.__class__.__name__,
                    attempt + 1,
                    max_retries,
                    delay,
                )
                time.sleep(delay)
            else:
                raise

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in RETRYABLE_STATUS_CODES:
                last_exception = exc
                if attempt < max_retries:
                    delay = _compute_delay(attempt, backoff_base, backoff_max)
                    log.debug(
                        "Retrying request (HTTP %s, attempt %d/%d, backoff %.1fs)",
                        exc.response.status_code,
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    raise
            else:
                raise

    if last_exception:
        raise last_exception
    raise RuntimeError("Retry logic exhausted")


def _compute_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff: base * 2^attempt, capped at cap."""
    return min(base * (2 ** attempt), cap)
