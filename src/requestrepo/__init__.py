import os
import jwt
import sys
import json
import base64
import asyncio
import requests
from websockets.client import connect
from typing import Union, Callable, Dict, List
from jwt.exceptions import DecodeError

class Requestrepo:
  """
  Class for interacting with the Requestrepo API. Provides real-time request monitoring,
  historical request retrieval, and the ability to delete requests.
  """

  __old_requests: List[Dict]

  def __init__(
        self,
        token: Union[str, None] = None,
        host: str = "requestrepo.com",
        port: int = 443,
        protocol: str = 'https',
        verify: bool = True
    ) -> None:
    """
    Initializes the Requestrepo client.

    Args:
        token: Optional API token. If not provided, attempts to fetch it from
                the REQUESTREPO_TOKEN environment variable or by calling the API.
        host: Hostname of the Requestrepo server (default: "requestrepo.com").
        port: Port of the Requestrepo server (default: 443).
        protocol: Protocol to use ('https' or 'http', default: 'https').
        verify: Whether to verify SSL certificates (default: True).
    """

    self.__host = host
    self.__port = port
    self.__protocol = protocol
    self.__verify = verify

    if not token:
      token = os.environ.get("REQUESTREPO_TOKEN")

    info: bool = not token

    if not token:
      token = requests.post(f"{protocol}://{host}:{port}/api/get_token", verify=verify).json()["token"]

    assert(token is not None)

    try:
      subdomain: str = jwt.decode(token, options={"verify_signature": False})["subdomain"]
    except DecodeError:
      subdomain: str = jwt.decode(token, verify=False)["subdomain"]

    if info:
      print(f"[+] Running on {subdomain}.{host} with token: {token}", file=sys.stderr)

    self.__token = token
    self.subdomain = subdomain
    self.domain = subdomain + "." + host

    if "https" == protocol.lower():
      wsp = "wss"
    else:
      wsp = "ws"

    self.__on_request_callback = self.on_request

    loop = asyncio.get_event_loop()
    self.__websocket = loop.run_until_complete(connect(f"{wsp}://{host}:{port}/api/ws"))
    loop.run_until_complete(self.__websocket.send(token))

    self.__old_requests = json.loads(loop.run_until_complete(self.__websocket.recv()))["data"]

  def on_request(self, data: Dict):
    """
    Callback function for handling new incoming requests. **Override this in your code.**

    Args:
        data: Dictionary containing the request data.
    """
    raise NotImplementedError(f"You must implement the on_request method on {self} class")

  def get_old_requests(self) -> List[Dict]:
    """
    Returns a list of previously received requests.

    Returns:
        List of dictionaries, each representing a past request.
    """
    return self.__old_requests

  def get_request(self) -> Dict:
    """
    Synchronously gets a single new request (blocks the current thread).

    Returns:
        Dictionary containing the request data.
    """
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(self.async_get_request())

  async def async_get_request(self) -> Dict:
    """
    Asynchronously gets a single new request.

    Returns:
        Dictionary containing the request data.
    """
    data = json.loads(await self.__websocket.recv())["data"]
    data = json.loads(data)
    data["raw"] = base64.b64decode(data["raw"])
    return data

  async def __process_requests(self):
      """
      Internal function to continuously receive and process requests asynchronously.
      """
      while True:
          request_data = await self.async_get_request()
          if self.__on_request_callback:
              self.__on_request_callback(request_data)

  def await_requests(self) -> None:
    """
    Starts listening for incoming requests indefinitely (doesn't return).
    """
    loop = asyncio.get_event_loop()
    loop.run_until_complete(self.__process_requests())

  def delete_request(self, id: str) -> bool:
    """
    Deletes a request from Requestrepo.

    Args:
        id: The ID of the request to delete.

    Returns:
        True if deletion was successful, False otherwise.
    """
    r = requests.post(f"{self.__protocol}://{self.__host}:{self.__port}/api/delete_request?token={self.__token}", json={"id": id}, verify=self.__verify)
    return r.status_code == 200

  def delete_all_requests(self) -> bool:
    """
    Deletes all requests associated with your account on Requestrepo.

    Returns:
        True if deletion was successful, False otherwise.
    """
    r = requests.post(f"{self.__protocol}://{self.__host}:{self.__port}/api/delete_all_requests?token={self.__token}", verify=self.__verify)
    return r.status_code == 200

  def response(self) -> Dict:
    """
    Returns a dictionary containing the response data.
    """
    r = requests.get(f"{self.__protocol}://{self.__host}:{self.__port}/api/get_file?token={self.__token}", verify=self.__verify)
    data = r.json()
    data["raw"] = base64.b64decode(data["raw"])
    # normalize list of headers to dictionary
    data["headers"] = {h["header"]:h["value"] for h in data["headers"]}

    return data

  def update_response(self, headers: Union[Dict, None] = None, raw: Union[bytes, None] = None, status_code: Union[int, None] = None) -> bool:
    """
    Updates the response data on remote.
    If a value is not provided, it will not be updated.
    """
    data = self.response()

    if headers:
      data["headers"] = headers
    if raw:
      data["raw"] = raw
    if status_code:
      data["status_code"] = status_code

    data["raw"] = base64.b64encode(data["raw"]).decode()

    # normalize dictionary of headers to list
    data["headers"] = [{"header":k, "value":v} for k,v in data["headers"].items()]

    r = requests.post(f"{self.__protocol}://{self.__host}:{self.__port}/api/update_file?token={self.__token}", json=data, verify=self.__verify)
    return r.status_code == 200

  def dns(self) -> List[Dict]:
    """
    Returns a dictionary containing the DNS data.
    """
    r = requests.get(f"{self.__protocol}://{self.__host}:{self.__port}/api/get_dns?token={self.__token}", verify=self.__verify)
    return r.json()

  def update_dns(self, dns: List[Dict]) -> bool:
    """
    Updates the DNS data on remote.
    """
    r = requests.post(f"{self.__protocol}://{self.__host}:{self.__port}/api/update_dns?token={self.__token}", json={"records":dns}, verify=self.__verify)
    return r.status_code == 200

  def add_dns(self, subsubdomain: str, dnstype: Union[int, str], value: str) -> bool:
    """
    Adds a DNS record to the remote.
    """
    dns_types = ["A", "AAAA", "CNAME", "TXT"]
    records = self.dns()

    # if dns entry already exists for same subsubdomain and dnstype, update it
    # otherwise, add a new entry
    for record in records:
      if record["domain"] == subsubdomain and record["type"] == dnstype:
        record["value"] = value
        return self.update_dns(records)

    if type(dnstype) == str:
      dnstype = dns_types.index(dnstype)
      if dnstype == -1:
        raise ValueError(f"Invalid DNS type: {dnstype}. Must be one of {dns_types}")
    elif type(dnstype) == int and (dnstype < 0 or dnstype > len(dns_types)):
      raise ValueError(f"Invalid DNS type: {dnstype}. Must be between 0 and {len(dns_types)-1}")

    records.append({"domain":subsubdomain, "type":dnstype, "value":value})

    return self.update_dns(records)


  def remove_dns(self, subsubdomain: str, dnstype: Union[int, str, None]) -> bool:
    """
    Removes a DNS record from the remote.
    """
    dns_types = ["A", "AAAA", "CNAME", "TXT"]

    records = self.dns()

    if type(dnstype) == str:
      dnstype = dns_types.index(dnstype)
      if dnstype == -1:
        raise ValueError(f"Invalid DNS type: {dnstype}. Must be one of {dns_types}")
    elif type(dnstype) == int and (dnstype < 0 or dnstype > len(dns_types)):
      raise ValueError(f"Invalid DNS type: {dnstype}. Must be between 0 and {len(dns_types)-1}")

    prev_len = len(records)

    records = [record for record in records if record["domain"] != subsubdomain and (dnstype == None or record["type"] != dnstype)]

    if len(records) == prev_len:
      return False

    return self.update_dns(records)


RequestRepo = Requestrepo
requestrepo = Requestrepo
