"""Data models for requestrepo v3 API.

Pydantic models for all request types, DNS records, and HTTP response
configurations. Fields are kept raw from the backend wherever possible.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class RequestType(str, Enum):
    """Type of captured request."""

    HTTP = "http"
    DNS = "dns"
    SMTP = "smtp"
    TCP = "tcp"


class DnsRecordType(str, Enum):
    """Supported DNS record types."""

    A = "A"
    AAAA = "AAAA"
    CNAME = "CNAME"
    TXT = "TXT"


# =============================================================================
# Request Models
# =============================================================================


class Request(BaseModel):
    """Base class for all captured requests.

    All fields come raw from the backend. Check ``.type`` or use
    ``isinstance()`` to determine the specific subclass.
    """

    id: str = Field(..., alias="_id")
    type: RequestType
    raw: bytes
    uid: str
    ip: str
    port: Optional[int] = None
    country: Optional[str] = None
    date: int

    model_config = {"populate_by_name": True}

    @property
    def timestamp(self) -> datetime:
        """Request time as a timezone-aware UTC datetime."""
        return datetime.fromtimestamp(self.date, tz=timezone.utc)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.id!r}, ip={self.ip!r})"

    def __str__(self) -> str:
        return repr(self)


class HttpRequest(Request):
    """An HTTP request captured by requestrepo.

    Example::

        req = repo.wait_for_http(timeout=30)
        print(req.method, req.url)
        print(req.headers.get("Authorization"))
        data = req.json()
    """

    type: Literal[RequestType.HTTP] = RequestType.HTTP
    method: str
    path: str
    query: Optional[str] = None
    url: str = ""
    protocol: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    @property
    def body(self) -> bytes:
        """Request body as bytes. Alias for ``.raw``."""
        return self.raw

    @property
    def text(self) -> str:
        """Request body decoded as UTF-8.

        Raises:
            UnicodeDecodeError: If the body is not valid UTF-8.
        """
        return self.raw.decode("utf-8")

    def json(self) -> Any:
        """Parse request body as JSON.

        Raises:
            ValueError: If the body is not valid JSON.
        """
        return json.loads(self.raw)

    @property
    def content_type(self) -> str | None:
        """Shortcut for ``headers.get("Content-Type")``."""
        return self.headers.get("Content-Type")

    def __repr__(self) -> str:
        path_display = self.path
        if self.query:
            path_display += self.query
        return f"HttpRequest({self.method} {path_display} from {self.ip})"

    def __str__(self) -> str:
        lines = [f"{self.method} {self.path}{self.query or ''} {self.protocol}"]
        for name, value in self.headers.items():
            lines.append(f"{name}: {value}")
        if self.raw:
            lines.append("")
            lines.append(self.raw.decode("utf-8", errors="replace")[:1000])
        return "\n".join(lines)


class DnsRequest(Request):
    """A DNS query captured by requestrepo.

    Example::

        req = repo.wait_for_dns(timeout=30)
        label = req.domain.split(".")[0]  # extract exfil data
    """

    type: Literal[RequestType.DNS] = RequestType.DNS
    query_type: str
    domain: str
    reply: Optional[str] = None

    def __repr__(self) -> str:
        return f"DnsRequest({self.query_type} {self.domain} from {self.ip})"


class SmtpAttachment(BaseModel):
    """An email attachment."""
    filename: str
    content_type: str
    size: int
    content: bytes  # decoded from base64 by Pydantic

    def __repr__(self) -> str:
        return f"SmtpAttachment({self.filename!r}, {self.content_type}, {self.size} bytes)"


class SmtpRequest(Request):
    """An SMTP email captured by requestrepo.

    Example::

        email = repo.wait_for_smtp(timeout=120)
        print(email.subject, email.sender)
    """

    type: Literal[RequestType.SMTP] = RequestType.SMTP
    command: str
    data: Optional[str] = None
    subject: Optional[str] = None
    sender: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    cc: Optional[str] = None
    bcc: Optional[str] = None
    text_body: Optional[str] = None
    html_body: Optional[str] = None
    attachments: list[SmtpAttachment] = Field(default_factory=list)

    @property
    def text(self) -> str | None:
        """Plain text email body.

        Returns text_body from server if available, otherwise
        falls back to client-side MIME parsing of the data field.
        """
        if self.text_body is not None:
            return self.text_body
        if self.data:
            return self._parse_mime_text()
        return None

    @property
    def html(self) -> str | None:
        """HTML email body.

        Returns html_body from server if available, otherwise
        falls back to client-side MIME parsing of the data field.
        """
        if self.html_body is not None:
            return self.html_body
        if self.data:
            return self._parse_mime_html()
        return None

    def _parse_mime_text(self) -> str | None:
        """Fallback: parse text/plain from MIME data."""
        import email as email_mod
        msg = email_mod.message_from_string(self.data or "")
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="replace")
        return None

    def _parse_mime_html(self) -> str | None:
        """Fallback: parse text/html from MIME data."""
        import email as email_mod
        msg = email_mod.message_from_string(self.data or "")
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")
        return None

    def __repr__(self) -> str:
        parts = ["SmtpRequest("]
        if self.sender:
            parts.append(f"from {self.sender}")
        if self.subject:
            parts.append(f"subj={self.subject!r}")
        parts.append(f"from {self.ip})")
        return " ".join(parts)


class TcpRequest(Request):
    """A raw TCP connection captured by requestrepo.

    Example::

        req = repo.wait_for_tcp(timeout=30)
        print(f"{len(req.raw)} bytes on port {req.server_port}")
    """

    type: Literal[RequestType.TCP] = RequestType.TCP
    server_port: int = Field(0, alias="port")

    # Override port from base to avoid conflict —
    # TcpRequest uses "port" from backend as server_port
    port: Optional[int] = Field(None, exclude=True)

    def __repr__(self) -> str:
        return (
            f"TcpRequest({len(self.raw)} bytes on :{self.server_port}"
            f" from {self.ip})"
        )


AnyRequest = Union[HttpRequest, DnsRequest, SmtpRequest, TcpRequest]
"""Union type of all request subtypes."""


# =============================================================================
# Configuration Models
# =============================================================================


class DnsRecord(BaseModel):
    """A DNS record for a requestrepo session.

    Constructor follows zone-file order: domain, type, value.

    Example::

        DnsRecord(domain="*", record_type="A", value="1.2.3.4")
        DnsRecord(domain="_verify", record_type="TXT", value="token=abc")
    """

    domain: str
    record_type: str = Field(..., alias="type")
    value: str

    model_config = {"populate_by_name": True}

    def __repr__(self) -> str:
        return f"DnsRecord({self.domain} {self.record_type} {self.value})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DnsRecord):
            return NotImplemented
        return (
            self.domain == other.domain
            and self.record_type == other.record_type
            and self.value == other.value
        )

    def __hash__(self) -> int:
        return hash((self.domain, self.record_type, self.value))


class Header(BaseModel):
    """HTTP header for custom responses."""

    header: str
    value: str


class Response(BaseModel):
    """Custom HTTP response configuration (internal wire format)."""

    raw: str
    headers: list[Header]
    status_code: int


class ResponseFile(BaseModel):
    """A custom HTTP response served at a URL path.

    Returned by :meth:`RequestRepo.get_responses`. Use
    :meth:`RequestRepo.set_response` to create/update responses.
    """

    path: str = ""
    body: bytes = b""
    status_code: int = 200
    headers: dict[str, str] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        """Body decoded as UTF-8."""
        return self.body.decode("utf-8", errors="replace")


__all__ = [
    "RequestType",
    "DnsRecordType",
    "Request",
    "HttpRequest",
    "DnsRequest",
    "SmtpAttachment",
    "SmtpRequest",
    "TcpRequest",
    "AnyRequest",
    "DnsRecord",
    "Header",
    "Response",
    "ResponseFile",
]
