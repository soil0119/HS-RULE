import os
import unittest
from unittest.mock import patch

from crawler_config import load_cookies


class CrawlerCookiesTest(unittest.TestCase):
    def test_reads_cookie_values_from_environment(self):
        with patch.dict(os.environ, {"HANSUNG_COOKIES_JSON": '{"JSESSIONID": "local-test-session"}'}):
            self.assertEqual(load_cookies(), {"JSESSIONID": "local-test-session"})

    def test_missing_configuration_uses_no_cookies(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_cookies(), {})

    def test_rejects_invalid_configuration_without_revealing_values(self):
        for value in ('not-json-private-value', '[]', '{"JSESSIONID": 123}'):
            with self.subTest(value=value), patch.dict(os.environ, {"HANSUNG_COOKIES_JSON": value}):
                with self.assertRaises(ValueError) as caught:
                    load_cookies()
                self.assertNotIn(value, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
