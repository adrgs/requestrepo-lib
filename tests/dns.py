import unittest
import requestrepo
import socket

class TestRequest(unittest.TestCase):
    def test_add_dns(self):
      r = requestrepo.RequestRepo()

      r.add_dns("testcanary1", "A", "1.1.1.1")

      ip = socket.gethostbyname(f"testcanary1.{r.domain}")

      self.assertEqual(ip, "1.1.1.1")

      r.add_dns("testcanary1", "A", "8.8.8.8")

      ip = socket.gethostbyname(f"{r.domain}")

      self.assertNotEqual(ip, "8.8.8.8")

if __name__ == '__main__':
  unittest.main()
