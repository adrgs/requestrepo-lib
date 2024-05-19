import unittest
import requestrepo
import requests

class TestResponse(unittest.TestCase):
    def test_update_raw(self):
        r = requestrepo.RequestRepo()

        raw = b'Hello world!'

        r.update_http(raw=raw)
        data = r.response()

        t = requests.get(f"http://{r.domain}/test")

        self.assertEqual(data.raw, raw)
        self.assertEqual(data.raw.decode(), t.text)


    def test_update_headers(self):
      r = requestrepo.RequestRepo()

      headers = {"test": "test"}

      r.update_http(headers=headers)
      data = r.response()

      self.assertEqual(data.headers, headers)


    def test_update_status_code(self):
      r = requestrepo.RequestRepo()

      status_code = 404

      r.update_http(status_code=status_code)
      data = r.response()

      self.assertEqual(data.status_code, status_code)


if __name__ == '__main__':
  unittest.main()
