## requestrepo Python client

Python bindings to automate requestrepo.com

**Installation**

```bash
pip install requestrepo
```

**Basic Usage**

1. **Instantiate the `Requestrepo` class:**

```python
from requestrepo import Requestrepo  # Requestrepo, RequestRepo and requestrepo are accepted imports

client = Requestrepo() # if token is not provided via the constructor or REQUESTREPO_TOKEN environment variable, a new one will be generated and printed to stderr
client = Requestrepo("your-token-here")
```

or

```bash
REQUESTREPO_TOKEN=token python your_script.py
```

**Examples**

**Example 1: Async request retrieval via `on_request`**

```python
from requestrepo import Requestrepo

def on_request(request_data: dict):
   print("New Request Received:", request_data)

client = Requestrepo(token="your-token-here")

client.await_requests()
```

**Example 2: Synchronous request retrieval**

```python
from requestrepo import Requestrepo

client = Requestrepo(token="your_api_token")

# Get the latest request (blocks until one is received)
new_request = client.get_request()
print("Latest Request:", new_request)
```

**Contributing**

Contributions are welcome! Please submit a pull request or open an issue if you have any ideas or suggestions.

**License**

MIT
