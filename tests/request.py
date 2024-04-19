import unittest
import requestrepo
import requests

class TestRequest(unittest.TestCase):
    def test_http_get_request(self):
        r = requestrepo.RequestRepo()
        t = requests.get(f"http://{r.domain}/test")

        request = r.get_request()
        while request["type"] != "http":
            request = r.get_request()

        self.assertEqual(request["method"], "GET")
        self.assertEqual(request["path"], "/test")

    def test_http_post_request(self):
        r = requestrepo.RequestRepo()
        t = requests.post(f"http://{r.domain}/test")

        request = r.get_request()
        while request["type"] != "http":
            request = r.get_request()

        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/test")

    def test_dns_request(self):
      r = requestrepo.RequestRepo()
      t = requests.get(f"http://testcanary1234.testcanary1234.{r.domain}/test")

      request = r.get_request()
      while request["type"] != "dns" or request["name"] == f"{r.domain}.":
          request = r.get_request()

      self.assertEqual(request["name"], f"testcanary1234.testcanary1234.{r.domain}.")

if __name__ == '__main__':
  unittest.main()
