import sys
from typing import Union, Dict, Optional
from pydantic import BaseModel, Field

if sys.version_info < (3, 11):
  from typing_extensions import NotRequired
else:
  from typing import NotRequired


class HttpRequest(BaseModel):
  _id: str
  type: str
  raw: bytes
  uid: str
  ip: str
  country: Optional[str] = Field(None)
  port: int
  date: int
  headers: Dict[str, str]
  method: str
  protocol: str
  path: str
  fragment: str
  query: str
  url: str


class DnsRequest(BaseModel):
  _id: str
  type: str
  raw: bytes
  uid: str
  ip: str
  country: Optional[str] = Field(None)
  port: int
  date: int
  dtype: str
  name: str
  reply: str


class HttpResponse(BaseModel):
  raw: bytes
  headers: Dict[str, str]
  status_code: int


class DnsRecord(BaseModel):
  type: Union[str, int]
  domain: str
  value: str