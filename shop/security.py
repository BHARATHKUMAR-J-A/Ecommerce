"""Authentication guards, CSRF tokens and other request-level defences."""

from __future__ import annotations

import functools
import secrets
import threading
import time
from typing import Callable
from urllib.parse import urljoin, urlparse

from flask import abort, flash, g, redirect, request, session, url_for

# Methods that change state must carry a CSRF token.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AttemptThrottle:
    """Sliding-window failure counter used to slow down password guessing.

    In-process only: it protects a single worker, not a load-balanced fleet.
    Put a real rate limiter at the edge before running this in production.
    """

    MAX_TRACKED_KEYS = 10_000

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key, times in list(self._hits.items()):
            kept = [t for t in times if t > cutoff]
            if kept:
                self._hits[key] = kept
            else:
                del self._hits[key]
        # Hard cap so a flood of distinct keys cannot exhaust memory.
        overflow = len(self._hits) - self.MAX_TRACKED_KEYS
        if overflow > 0:
            for key in sorted(self._hits, key=lambda k: self._hits[k][-1])[:overflow]:
                del self._hits[key]

    def retry_after(self, *keys: str) -> int:
        """Seconds the caller must wait, or 0 if they may try now."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            wait = 0.0
            for key in keys:
                times = self._hits.get(key, ())
                if len(times) >= self.max_attempts:
                    wait = max(wait, times[0] + self.window_seconds - now)
        return int(wait) + 1 if wait > 0 else 0

    def record_failure(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            for key in keys:
                self._hits.setdefault(key, []).append(now)

    def reset(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._hits.pop(key, None)


def get_throttle(name: str, max_attempts: int, window_seconds: int) -> AttemptThrottle:
    """Named throttle kept on the app, so separate apps (and tests) stay isolated."""
    from flask import current_app

    throttles = current_app.extensions.setdefault("throttles", {})
    if name not in throttles:
        throttles[name] = AttemptThrottle(max_attempts, window_seconds)
    return throttles[name]


def csrf_token() -> str:
    """Per-session token, created lazily and reused for the session's lifetime."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def verify_csrf() -> None:
    if request.method not in UNSAFE_METHODS:
        return
    expected = session.get("_csrf_token")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    if not expected or not secrets.compare_digest(expected, supplied):
        abort(400, description="Invalid or missing CSRF token. Please reload and retry.")


def login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def safe_redirect_target(target: str | None, fallback_endpoint: str = "catalog.index") -> str:
    """Reject absolute/off-site URLs so ?next= cannot be used as an open redirect."""
    if not target:
        return url_for(fallback_endpoint)
    candidate = urlparse(urljoin(request.host_url, target))
    host_root = urlparse(request.host_url)
    if candidate.scheme in ("http", "https") and candidate.netloc == host_root.netloc:
        return candidate.path + (("?" + candidate.query) if candidate.query else "")
    return url_for(fallback_endpoint)


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; form-action 'self'; base-uri 'self'; frame-ancestors 'none'",
    )
    return response
