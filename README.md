# requestrepo Python client

Python bindings to automate requestrepo.com

## Installation

```bash
pip install requestrepo
```

## Basic Usage

**Instantiate the `Requestrepo` class:**

```python
from requestrepo import Requestrepo  # Requestrepo, RequestRepo and requestrepo are accepted imports

client = Requestrepo() # new token printed to console
client = Requestrepo("your-token-here")
```

or via environment variables:

```bash
REQUESTREPO_TOKEN=token python your_script.py
```

## Examples

**Example 1: Async request retrieval via `on_request`**

```python
from requestrepo import Requestrepo

def on_request(request_data: dict):
   print("New Request Received:", request_data)

client = Requestrepo(token="your-token-here")

print(client.subdomain) # abcd1234
print(client.domain) # abcd1234.requestrepo.com

client.await_requests()
```

**Example 2: Synchronous request retrieval**

```python
from requestrepo import Requestrepo

client = Requestrepo(token="your_api_token")

print(client.subdomain) # abcd1234
print(client.domain) # abcd1234.requestrepo.com

# Get the latest request (blocks until one is received)
new_request = client.get_request()
print("Latest Request:", new_request)
```

**Example 3: Retrieve old requests**

You can iterate over all requests that are stored on the server:

```python
from requestrepo import Requestrepo

client = Requestrepo(token="your_api_token")

for request in client.get_old_requests():
    print("Request:", request)

client.delete_all_requests() # clear all requests on the server
```

## Contributing

Contributions are welcome! Please submit a pull request or open an issue if you have any ideas or suggestions.

## License

MIT
