import os
import subprocess
import sys
import time
import unittest

import jwt
from fastapi.testclient import TestClient

KEY = "test-only-signing-key-" + "a" * 24
PASSWORD = "test-only-administrator-password"


def configured_client(**kwargs):
    from hsrule_web.settings import Settings
    from server import create_app

    settings = Settings(admin_id="test-admin", admin_pw=PASSWORD, secret_key=KEY,
                        allowed_hosts=("testserver",))
    return TestClient(create_app(settings, **kwargs), raise_server_exceptions=False)


class WebAuthTests(unittest.TestCase):
    def test_import_and_ui_without_runtime_dependencies(self):
        env = {key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT") if key in os.environ}
        code = """
import sys
from fastapi.testclient import TestClient
from hsrule_web.settings import Settings
from server import app, create_app
assert 'sentence_transformers' not in sys.modules and 'groq' not in sys.modules
client = TestClient(create_app(Settings(allowed_hosts=('testserver',))))
for path in ('/', '/login-page', '/upload', '/HSU_logo.png', '/health/live'):
    assert client.get(path).status_code == 200, path
readiness = client.get('/health')
assert readiness.status_code == 503 and readiness.json()['code'] == 'not_ready'
"""
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_placeholder_settings_disable_login(self):
        from hsrule_web.settings import Settings
        from server import create_app

        for password, key in (("", ""), ("1234", KEY),
                              ("replace-with-a-strong-password", KEY),
                              (PASSWORD, "replace-with-a-long-random-secret-" * 2)):
            with self.subTest(password=password, key=key):
                settings = Settings(admin_id="admin", admin_pw=password, secret_key=key,
                                    allowed_hosts=("testserver",))
                response = TestClient(create_app(settings)).post(
                    "/login", json={"username": "admin", "password": "1234"}
                )
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "admin_not_configured")

    def test_login_rejects_forged_expired_wrong_subject_issuer_audience_and_future_tokens(self):
        client = configured_client()
        response = client.post("/login", json={"username": "test-admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["token"]
        claims = jwt.decode(token, KEY, algorithms=["HS256"],
                            options={"verify_aud": False})
        auth = {"Authorization": "Bearer " + token}
        self.assertEqual(client.get("/uploaded-rules", headers=auth).status_code, 503)
        now = int(time.time())
        variants = [
            jwt.encode(claims, "forged-key-" * 4, algorithm="HS256"),
            jwt.encode({**claims, "exp": now - 1}, KEY, algorithm="HS256"),
            jwt.encode({**claims, "sub": "wrong"}, KEY, algorithm="HS256"),
            jwt.encode({**claims, "iss": "wrong"}, KEY, algorithm="HS256"),
            jwt.encode({**claims, "aud": "wrong"}, KEY, algorithm="HS256"),
            jwt.encode({**claims, "iat": now + 60}, KEY, algorithm="HS256"),
            jwt.encode({key: value for key, value in claims.items() if key != "iat"},
                       KEY, algorithm="HS256"),
            "invalid",
        ]
        for forged in variants:
            with self.subTest(token=forged):
                response = client.get("/uploaded-rules", headers={"Authorization": "Bearer " + forged})
                self.assertEqual(response.status_code, 401, response.text)
                self.assertEqual(response.json()["code"], "invalid_token")
        response = client.get("/uploaded-rules")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_login_attempts_are_limited_by_peer_even_with_different_forwarded_ips(self):
        client = configured_client()
        for number in range(5):
            response = client.post("/login", json={"username": "test-admin", "password": "bad"},
                                   headers={"X-Forwarded-For": f"192.0.2.{number + 1}"})
            self.assertEqual(response.status_code, 401, response.text)
        response = client.post("/login", json={"username": "test-admin", "password": PASSWORD},
                               headers={"X-Forwarded-For": "192.0.2.240"})
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["code"], "login_rate_limited")
        self.assertGreater(int(response.headers["retry-after"]), 0)

    def test_host_and_cross_origin_policy(self):
        client = configured_client()
        response = client.get("/health/live", headers={"Host": "elsewhere.example"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_host")
        response = client.get("/health/live", headers={"Host": "wrong@testserver"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_host")
        for origin, expected in (("https://hs.officeworkkt.com", 200),
                                 ("http://localhost:5173", 200),
                                 ("https://elsewhere.example", 400)):
            response = client.options("/login", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            })
            self.assertEqual(response.status_code, expected, response.text)
            if expected == 200:
                self.assertEqual(response.headers["access-control-allow-origin"], origin)

    def test_wildcard_public_origin_configuration_cannot_enable_wildcard_cors(self):
        from hsrule_web.settings import Settings
        from server import create_app

        client = TestClient(create_app(Settings(public_origin="*", allowed_hosts=("testserver",))))
        response = client.options("/login", headers={
            "Origin": "https://elsewhere.example", "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_login_is_disabled_for_credentials_beyond_the_request_limits(self):
        from hsrule_web.settings import Settings
        from server import create_app

        for username, password in (("u" * 257, PASSWORD), ("admin", "p" * 1025)):
            with self.subTest(username_length=len(username), password_length=len(password)):
                settings = Settings(admin_id=username, admin_pw=password, secret_key=KEY,
                                    allowed_hosts=("testserver",))
                client = TestClient(create_app(settings))
                response = client.post("/login", json={"username": "admin", "password": PASSWORD})
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["code"], "admin_not_configured")
