"""requestrepo v3.0.0 — Python client for requestrepo.com

Capture HTTP, DNS, SMTP, and TCP requests in real-time.
Built for AI agents, security researchers, and automation.

Quick start::

    from requestrepo import RequestRepo

    with RequestRepo() as repo:
        print(f"Send requests to: {repo.url}")
        req = repo.wait_for_http(timeout=30)
        print(req.method, req.path, req.text)
"""

import base64
import json
import logging
import threading
import time
from typing import Any, Callable, Iterator, Union

logger = logging.getLogger("requestrepo")

import requests as http_lib
from websockets.sync.client import connect

from .exceptions import (
    APIError,
    AuthError,
    NotFoundError,
    RateLimitError,
    RequestRepoConnectionError,
    RequestRepoError,
    RequestRepoTimeoutError,
    SessionError,
)
from .models import (
    AnyRequest,
    DnsRecord,
    DnsRecordType,
    DnsRequest,
    HttpRequest,
    Request,
    RequestType,
    Response,
    ResponseFile,
    SmtpAttachment,
    SmtpRequest,
    TcpRequest,
)


class RequestRepo:
    """Client for the requestrepo API.

    Creates a session with a unique subdomain and connects via WebSocket
    for real-time request notifications. Use as a context manager for
    automatic cleanup.

    **3-line example** (for AI agents)::

        with RequestRepo() as repo:
            print(f"Inject: {repo.url}/callback")
            req = repo.wait_for_http(timeout=30)

    Args:
        token: Existing JWT to reuse a session. Omit to create a new one.
        admin_token: Required by some instances for session creation.
        host: Server hostname.
        port: Server port.
        protocol: ``"http"`` or ``"https"``.
        verify: Verify TLS certificates.

    Raises:
        SessionError: If session creation fails.
        AuthError: If the provided token is invalid.
        RequestRepoConnectionError: If WebSocket connection fails.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        admin_token: str | None = None,
        host: str = "requestrepo.com",
        port: int = 443,
        protocol: str = "https",
        verify: bool = True,
    ) -> None:
        self.__host = host
        self.__port = port
        self.__protocol = protocol
        self.__verify = verify
        self.__websocket: Any = None
        self.__ws_thread: threading.Thread | None = None
        self.__ws_running = False
        self.__request_queue: list[AnyRequest] = []
        self.__request_lock = threading.Lock()
        self.__request_event = threading.Event()
        self.__pong_event = threading.Event()

        if not token:
            body: dict[str, Any] = {}
            if admin_token:
                body["admin_token"] = admin_token
            try:
                resp = http_lib.post(
                    f"{self._base_url()}/api/v2/sessions",
                    json=body,
                    verify=verify,
                )
            except http_lib.ConnectionError as e:
                raise RequestRepoConnectionError(
                    f"Failed to connect to {host}:{port}: {e}"
                ) from e
            if resp.status_code == 429:
                retry = resp.json().get("retry_after", 60)
                raise RateLimitError(
                    "Rate limited during session creation", retry_after=retry
                )
            if resp.status_code == 401:
                raise AuthError("Admin token required or invalid")
            if resp.status_code not in (200, 201):
                raise SessionError(
                    f"Session creation failed: {resp.status_code} {resp.text}"
                )
            data = resp.json()
            token = data["token"]
            self.__subdomain = data["subdomain"]
        else:
            self.__subdomain = self._extract_subdomain(token)
            if not self.__subdomain:
                raise AuthError(
                    "Invalid token: could not extract subdomain from JWT"
                )

        self.__token = token

        # Connect WebSocket in background
        self._connect_websocket()
        self.__ws_running = True
        self.__ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self.__ws_thread.start()

    # =========================================================================
    # Context Manager
    # =========================================================================

    def __enter__(self) -> "RequestRepo":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close WebSocket and release resources. Idempotent."""
        self.__ws_running = False
        if self.__websocket:
            try:
                self.__websocket.close()
            except Exception:
                pass
            self.__websocket = None
        # Wake up any threads blocked on wait_for_*
        self.__request_event.set()

    def __repr__(self) -> str:
        return (
            f"RequestRepo({self.domain}, connected={self.connected})"
        )

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def subdomain(self) -> str:
        """Session identifier (e.g., ``"abc12345"``)."""
        return self.__subdomain

    @property
    def domain(self) -> str:
        """Session domain (e.g., ``"abc12345.requestrepo.com"``).

        Use for DNS queries, email addresses, URL construction.
        """
        return f"{self.__subdomain}.{self.__host}"

    @property
    def url(self) -> str:
        """Base URL for this session.

        Example: ``"https://abc12345.requestrepo.com"``

        Append paths: ``f"{repo.url}/callback"``
        """
        default_port = 443 if self.__protocol == "https" else 80
        port_suffix = "" if self.__port == default_port else f":{self.__port}"
        return f"{self.__protocol}://{self.__subdomain}.{self.__host}{port_suffix}"

    @property
    def email(self) -> str:
        """Catch-all email address for SMTP capture.

        Example: ``"test@abc12345.requestrepo.com"``

        Any address ``*@{domain}`` will be captured.
        """
        return f"test@{self.domain}"

    @property
    def token(self) -> str:
        """JWT session token. Save and reuse: ``RequestRepo(token=...)``."""
        return self.__token

    @property
    def connected(self) -> bool:
        """Whether the WebSocket connection is alive."""
        return self.__ws_running and self.__websocket is not None

    # =========================================================================
    # Wait for Requests (real-time via WebSocket)
    # =========================================================================

    def wait_for_request(
        self,
        timeout: float | None = 30,
        *,
        match: Callable[[AnyRequest], bool] | None = None,
    ) -> AnyRequest:
        """Block until a request arrives via WebSocket.

        Non-matching requests stay in the queue for later calls.

        Args:
            timeout: Seconds to wait. ``None`` = wait forever.
                ``0`` = non-blocking check (return queued match or raise).
            match: Optional predicate. Only requests where
                ``match(req)`` returns True are returned.

        Returns:
            The first matching request.

        Raises:
            RequestRepoTimeoutError: No match arrived before timeout.
            RequestRepoConnectionError: WebSocket disconnected.
        """
        if timeout == 0:
            # Non-blocking: check queue only
            result = self._dequeue_match(match)
            if result is not None:
                return result
            raise RequestRepoTimeoutError("No matching request in queue")

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            # Ordering guarantee: we always check queue + ws_running
            # AFTER waking from event.wait(). This ensures we never miss
            # a disconnect signal even if it fires during wait().
            result = self._dequeue_match(match)
            if result is not None:
                return result

            if not self.__ws_running:
                raise RequestRepoConnectionError("WebSocket disconnected")

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestRepoTimeoutError(
                        f"No matching request within {timeout}s"
                    )
            else:
                remaining = None

            self.__request_event.wait(timeout=remaining)
            self.__request_event.clear()

    def wait_for_http(
        self,
        timeout: float | None = 30,
        *,
        method: str | None = None,
        path: str | None = None,
        match: Callable[["HttpRequest"], bool] | None = None,
    ) -> "HttpRequest":
        """Block until an HTTP request arrives.

        Args:
            timeout: Seconds to wait (default 30). ``None`` = forever.
            method: Match exact HTTP method (case-insensitive).
            path: Prefix match on request path. Leading ``/`` auto-added.
            match: Additional custom predicate.

        Returns:
            The matching HttpRequest.

        Raises:
            RequestRepoTimeoutError: No match within timeout.

        Examples::

            repo.wait_for_http(timeout=30)
            repo.wait_for_http(method="POST", path="/callback", timeout=60)
            repo.wait_for_http(match=lambda r: "secret" in r.text, timeout=30)
        """
        # Normalize path
        if path is not None and not path.startswith("/"):
            path = f"/{path}"

        def predicate(req: AnyRequest) -> bool:
            if not isinstance(req, HttpRequest):
                return False
            if method is not None and req.method.upper() != method.upper():
                return False
            if path is not None and not req.path.startswith(path):
                return False
            if match is not None and not match(req):
                return False
            return True

        return self.wait_for_request(timeout=timeout, match=predicate)  # type: ignore[return-value]

    def wait_for_dns(
        self,
        timeout: float | None = 30,
        *,
        query_type: str | None = None,
        domain: str | None = None,
        match: Callable[["DnsRequest"], bool] | None = None,
    ) -> "DnsRequest":
        """Block until a DNS query arrives.

        Args:
            timeout: Seconds to wait (default 30). ``None`` = forever.
            query_type: Match exact query type (e.g., ``"A"``, ``"TXT"``).
            domain: Substring match in the queried domain name.
            match: Additional custom predicate.

        Returns:
            The matching DnsRequest.

        Raises:
            RequestRepoTimeoutError: No match within timeout.
        """

        def predicate(req: AnyRequest) -> bool:
            if not isinstance(req, DnsRequest):
                return False
            if query_type is not None and req.query_type != query_type:
                return False
            if domain is not None and domain not in req.domain:
                return False
            if match is not None and not match(req):
                return False
            return True

        return self.wait_for_request(timeout=timeout, match=predicate)  # type: ignore[return-value]

    def wait_for_smtp(
        self,
        timeout: float | None = 60,
        *,
        subject: str | None = None,
        sender: str | None = None,
        match: Callable[["SmtpRequest"], bool] | None = None,
    ) -> "SmtpRequest":
        """Block until an SMTP email arrives.

        Default timeout is 60s (emails can be slow).

        Args:
            timeout: Seconds to wait (default 60). ``None`` = forever.
            subject: Substring match in subject line.
            sender: Substring match in sender address.
            match: Additional custom predicate.

        Returns:
            The matching SmtpRequest.

        Raises:
            RequestRepoTimeoutError: No match within timeout.
        """

        def predicate(req: AnyRequest) -> bool:
            if not isinstance(req, SmtpRequest):
                return False
            if subject is not None and (
                req.subject is None or subject not in req.subject
            ):
                return False
            if sender is not None and (
                req.sender is None or sender not in req.sender
            ):
                return False
            if match is not None and not match(req):
                return False
            return True

        return self.wait_for_request(timeout=timeout, match=predicate)  # type: ignore[return-value]

    def wait_for_email(
        self,
        timeout: float | None = 60,
        *,
        subject: str | None = None,
        sender: str | None = None,
        match: Callable[["SmtpRequest"], bool] | None = None,
    ) -> "SmtpRequest":
        """Wait for an email. Alias for wait_for_smtp().

        Uses 'email' instead of 'smtp' for clarity with AI agents.
        """
        return self.wait_for_smtp(
            timeout=timeout, subject=subject, sender=sender, match=match
        )

    def wait_for_tcp(
        self,
        timeout: float | None = 30,
        *,
        match: Callable[["TcpRequest"], bool] | None = None,
    ) -> "TcpRequest":
        """Block until a TCP connection is captured.

        Args:
            timeout: Seconds to wait (default 30). ``None`` = forever.
            match: Additional custom predicate.

        Returns:
            The matching TcpRequest.

        Raises:
            RequestRepoTimeoutError: No match within timeout.
        """

        def predicate(req: AnyRequest) -> bool:
            if not isinstance(req, TcpRequest):
                return False
            if match is not None and not match(req):
                return False
            return True

        return self.wait_for_request(timeout=timeout, match=predicate)  # type: ignore[return-value]

    # =========================================================================
    # Streaming Iterator
    # =========================================================================

    def listen(
        self,
        timeout: float | None = None,
        *,
        match: Callable[[AnyRequest], bool] | None = None,
    ) -> Iterator[AnyRequest]:
        """Iterate over incoming requests in real-time.

        Blocks on each iteration waiting for the next request. Stops when
        ``timeout`` total seconds have elapsed, the WebSocket disconnects,
        or the caller breaks.

        Args:
            timeout: Total wall-clock seconds. ``None`` = listen forever.
            match: Predicate to filter which requests are yielded.

        Yields:
            Matching requests as they arrive.

        Example::

            for req in repo.listen(timeout=300):
                if isinstance(req, HttpRequest):
                    print(f"{req.method} {req.path}")
        """
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
            else:
                remaining = None

            try:
                req = self.wait_for_request(timeout=remaining, match=match)
                yield req
            except RequestRepoTimeoutError:
                return
            except RequestRepoConnectionError:
                return

    # =========================================================================
    # Historical Requests (REST API)
    # =========================================================================

    def list_requests(
        self, limit: int = 100, offset: int = 0
    ) -> list[AnyRequest]:
        """Fetch stored requests via REST API.

        Args:
            limit: Maximum number of requests (1-1000).
            offset: Number of requests to skip.

        Returns:
            List of request objects.
        """
        r = self._api_get(
            "/api/v2/requests", params={"limit": limit, "offset": offset}
        )
        return [self._parse_request(req) for req in r.get("requests", [])]

    def get_request(self, request_id: str) -> AnyRequest:
        """Fetch a single request by ID.

        Raises:
            NotFoundError: If no request exists with that ID.
        """
        r = self._api_get(f"/api/v2/requests/{request_id}")
        return self._parse_request(r)

    def delete_request(self, request_id: str) -> None:
        """Delete a single captured request."""
        self._api_delete(f"/api/v2/requests/{request_id}")

    def clear_requests(self) -> None:
        """Delete all captured requests for this session."""
        self._api_delete("/api/v2/requests")

    # =========================================================================
    # DNS Records
    # =========================================================================

    def get_dns(self) -> list[DnsRecord]:
        """Get all DNS records for this session."""
        r = self._api_get("/api/v2/dns")
        records = []
        for d in r.get("records", []):
            if d.get("domain") == "":
                d["domain"] = "@"
            records.append(DnsRecord(**d))
        return records

    def set_dns(self, *records: DnsRecord) -> None:
        """Replace ALL DNS records for this session.

        Example::

            repo.set_dns(
                DnsRecord(domain="*", record_type="A", value="1.2.3.4"),
                DnsRecord(domain="mail", record_type="TXT", value="v=spf1 ~all"),
            )
        """
        payload = []
        for d in records:
            record = {"type": d.record_type, "domain": d.domain, "value": d.value}
            if record["domain"] == "@":
                record["domain"] = ""
            payload.append(record)
        self._api_put("/api/v2/dns", json={"records": payload})

    def add_dns(self, domain: str, record_type: str, value: str) -> None:
        """Add a DNS record (preserves all existing records).

        Fetches current records, appends the new one, PUTs back.
        Truly additive — DNS supports multiple records per type+domain.

        Args:
            domain: Subdomain label (``"*"``, ``"www"``, ``"_verify"``).
            record_type: ``"A"``, ``"AAAA"``, ``"CNAME"``, or ``"TXT"``.
            value: Record value.

        Example::

            repo.add_dns("*", "A", "1.2.3.4")
            repo.add_dns("_verify", "TXT", "token=abc")
        """
        records = self.get_dns()
        records.append(
            DnsRecord(domain=domain, record_type=record_type, value=value)
        )
        self.set_dns(*records)

    def remove_dns(
        self, domain: str, record_type: str | None = None
    ) -> None:
        """Remove DNS records matching domain (and optionally type).

        Fetches current records, filters, PUTs back.
        """
        records = self.get_dns()
        if record_type:
            filtered = [
                r
                for r in records
                if not (r.domain == domain and r.record_type == record_type)
            ]
        else:
            filtered = [r for r in records if r.domain != domain]
        self.set_dns(*filtered)

    # =========================================================================
    # HTTP Responses
    # =========================================================================

    def get_responses(self) -> dict[str, ResponseFile]:
        """Get all custom HTTP responses for this session."""
        r = self._api_get("/api/v2/files")
        result = {}
        for path, data in r.items():
            body = base64.b64decode(data.get("raw", ""))
            headers = {
                h["header"]: h["value"] for h in data.get("headers", [])
            }
            result[path] = ResponseFile(
                path=path,
                body=body,
                status_code=data.get("status_code", 200),
                headers=headers,
            )
        return result

    def set_response(
        self,
        path: str,
        body: str | bytes = "",
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Set a custom HTTP response at a URL path.

        Additive: preserves responses at other paths. Leading ``/``
        stripped automatically.

        Args:
            path: URL path (e.g., ``"index.html"``, ``"api/data.json"``).
            body: Response body (string or bytes).
            status_code: HTTP status code.
            headers: Response headers as dict.

        Examples::

            repo.set_response("index.html", "<h1>hello</h1>")
            repo.set_response("api/data", '{"ok":true}',
                headers={"Content-Type": "application/json"})
            repo.set_response("redir", "", status_code=302,
                headers={"Location": "https://evil.com"})
        """
        path = path.lstrip("/")

        # Get existing files
        r = self._api_get("/api/v2/files")

        # Encode body
        if isinstance(body, str):
            raw = base64.b64encode(body.encode("utf-8")).decode("utf-8")
        else:
            raw = base64.b64encode(body).decode("utf-8")

        # Build header list
        header_list = []
        if headers:
            for name, value in headers.items():
                header_list.append({"header": name, "value": value})

        r[path] = {
            "raw": raw,
            "headers": header_list,
            "status_code": status_code,
        }
        self._api_put("/api/v2/files", json=r)

    def remove_response(self, path: str) -> None:
        """Remove a custom HTTP response at a path."""
        path = path.lstrip("/")
        r = self._api_get("/api/v2/files")
        if path in r:
            del r[path]
            self._api_put("/api/v2/files", json=r)

    # =========================================================================
    # Sharing
    # =========================================================================

    def share(self, request_id: str) -> str:
        """Get a shareable URL for a request.

        Returns:
            Full URL for the shared request.
        """
        r = self._api_post(f"/api/v2/requests/{request_id}/share")
        share_token = r.get("share_token", "")
        return f"{self._base_url()}/api/v2/requests/shared/{share_token}"

    def get_shared(self, share_token: str) -> AnyRequest:
        """Fetch a shared request by token. Reuses this client's connection."""
        resp = http_lib.get(
            f"{self._base_url()}/api/v2/requests/shared/{share_token}",
            verify=self.__verify,
        )
        self._check_response(resp)
        return self._parse_request(resp.json())

    # =========================================================================
    # Internal: HTTP API
    # =========================================================================

    def _base_url(self) -> str:
        return f"{self.__protocol}://{self.__host}:{self.__port}"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.__token}"}

    def _check_response(self, resp: http_lib.Response) -> None:
        """Check HTTP response and raise appropriate exception."""
        if 200 <= resp.status_code < 300:
            return
        if resp.status_code == 401:
            raise AuthError("Invalid or expired token")
        if resp.status_code == 404:
            raise NotFoundError("Resource not found")
        if resp.status_code == 429:
            data = resp.json() if resp.text else {}
            raise RateLimitError(
                "Rate limit exceeded",
                retry_after=data.get("retry_after", 60),
            )
        raise APIError(
            f"API error: {resp.status_code} {resp.text}",
            status_code=resp.status_code,
            code=resp.json().get("code", "") if resp.text else "",
        )

    def _api_get(self, path: str, params: dict | None = None) -> Any:
        resp = http_lib.get(
            f"{self._base_url()}{path}",
            params=params,
            headers=self._auth_headers(),
            verify=self.__verify,
        )
        self._check_response(resp)
        return resp.json()

    def _api_put(self, path: str, json: Any = None) -> Any:
        resp = http_lib.put(
            f"{self._base_url()}{path}",
            headers=self._auth_headers(),
            json=json,
            verify=self.__verify,
        )
        self._check_response(resp)
        return resp.json() if resp.text else None

    def _api_post(self, path: str, json: Any = None) -> Any:
        resp = http_lib.post(
            f"{self._base_url()}{path}",
            headers=self._auth_headers(),
            json=json,
            verify=self.__verify,
        )
        self._check_response(resp)
        return resp.json() if resp.text else None

    def _api_delete(self, path: str) -> None:
        resp = http_lib.delete(
            f"{self._base_url()}{path}",
            headers=self._auth_headers(),
            verify=self.__verify,
        )
        self._check_response(resp)

    # =========================================================================
    # Internal: Request Parsing
    # =========================================================================

    def _parse_request(self, data: dict) -> AnyRequest:
        """Parse a request dict from the API into the appropriate model."""
        req_type = data.get("type")

        # Decode base64 fields
        if "raw" in data and isinstance(data["raw"], str):
            data["raw"] = base64.b64decode(data["raw"])
        if "body" in data and isinstance(data["body"], str):
            data["body"] = base64.b64decode(data["body"])

        if req_type == "http":
            return HttpRequest(**data)
        elif req_type == "dns":
            return DnsRequest(**data)
        elif req_type == "smtp":
            return SmtpRequest(**data)
        elif req_type == "tcp":
            return TcpRequest(**data)
        else:
            raise ValueError(f"Unknown request type: {req_type}")

    def _dequeue_match(
        self, match: Callable[[AnyRequest], bool] | None
    ) -> AnyRequest | None:
        """Check queue for a matching request and remove it."""
        with self.__request_lock:
            for i, req in enumerate(self.__request_queue):
                if match is None or match(req):
                    return self.__request_queue.pop(i)
        return None

    # =========================================================================
    # Internal: WebSocket
    # =========================================================================

    @staticmethod
    def _extract_subdomain(token: str) -> str:
        """Extract subdomain from JWT payload."""
        try:
            payload = token.split(".")[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            decoded = base64.urlsafe_b64decode(payload)
            data = json.loads(decoded)
            return data.get("subdomain", "")
        except (IndexError, ValueError, json.JSONDecodeError):
            return ""

    def _connect_websocket(self) -> None:
        """Connect to the WebSocket."""
        ws_protocol = "wss" if self.__protocol == "https" else "ws"
        try:
            self.__websocket = connect(
                f"{ws_protocol}://{self.__host}:{self.__port}/api/v2/ws"
            )
        except Exception as e:
            raise RequestRepoConnectionError(
                f"WebSocket connection failed: {e}"
            ) from e

        self.__websocket.send(
            json.dumps({"cmd": "connect", "token": self.__token})
        )

        msg = json.loads(self.__websocket.recv())
        if msg.get("cmd") == "error":
            raise AuthError(f"WebSocket auth failed: {msg.get('message')}")

    def _ws_loop(self) -> None:
        """Internal WebSocket message loop with auto-reconnect.

        Runs in a daemon thread. On disconnect, retries with exponential
        backoff (1s, 2s, 4s, 8s, max 30s). Stops when close() is called.
        """
        backoff = 1.0
        max_backoff = 30.0

        while self.__ws_running:
            try:
                if not self.__websocket:
                    self._connect_websocket()
                    backoff = 1.0  # Reset on successful connect
                    logger.debug("WebSocket connected")

                raw_msg = self.__websocket.recv()
                msg = json.loads(raw_msg)
                cmd = msg.get("cmd")

                if cmd == "request":
                    self._enqueue_request(msg.get("data", {}))
                elif cmd == "requests":
                    for req_data in msg.get("data", []):
                        self._enqueue_request(req_data)
                elif cmd == "pong":
                    self.__pong_event.set()

            except Exception as e:
                if not self.__ws_running:
                    break
                logger.debug("WebSocket error: %s, reconnecting in %ss", e, backoff)
                self.__websocket = None
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

        self.__ws_running = False
        self.__request_event.set()  # Wake up waiting threads

    def ping(self, timeout: float = 5.0) -> bool:
        """Check if WebSocket connection is alive.

        Sends a ping and waits for pong response.

        Args:
            timeout: Seconds to wait for pong.

        Returns:
            True if pong received, False if timeout or disconnected.
        """
        if not self.__websocket or not self.__ws_running:
            return False
        try:
            self.__pong_event.clear()
            self.__websocket.send(json.dumps({"cmd": "ping"}))
            return self.__pong_event.wait(timeout=timeout)
        except Exception:
            return False

    def _enqueue_request(self, data: dict) -> None:
        """Parse and add a request to the queue, then signal waiters."""
        try:
            request = self._parse_request(data)
            with self.__request_lock:
                self.__request_queue.append(request)
            self.__request_event.set()
        except Exception:
            logger.warning("Failed to parse request: %s", data)


__all__ = [
    # Client
    "RequestRepo",
    # Exceptions
    "RequestRepoError",
    "AuthError",
    "SessionError",
    "RateLimitError",
    "RequestRepoConnectionError",
    "RequestRepoTimeoutError",
    "NotFoundError",
    "APIError",
    # Enums
    "RequestType",
    "DnsRecordType",
    # Request models
    "Request",
    "HttpRequest",
    "DnsRequest",
    "SmtpAttachment",
    "SmtpRequest",
    "TcpRequest",
    "AnyRequest",
    # Config models
    "DnsRecord",
    "ResponseFile",
]
