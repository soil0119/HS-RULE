"""Check the public deployment's TLS, navigation, assets and publish boundary."""

import hashlib
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build/hs-rule-ui"
ORIGIN = "https://hs.officeworkkit.com"
ROUTES = {"/": "index.html", "/login-page": "login.html", "/upload": "upload.html"}
PRIVATE_PATHS = ("/.env", "/server.py", "/Hansung_AX-update_crawler.zip", "/_headers", "/_redirects")


def check(item):
    path, expected = item
    with tempfile.TemporaryDirectory(prefix="hs-rule-http-") as directory:
        output = Path(directory) / "response"
        response = subprocess.run(
            ["curl", "--disable", "--silent", "--show-error", "--location", "--max-redirs", "5",
             "--connect-timeout", "10", "--max-time", "25", "--output", str(output),
             "--write-out", "%{http_code}\t%{url_effective}", ORIGIN + path],
            capture_output=True, text=True,
        )
        if response.returncode:
            raise RuntimeError(f"{path}: {response.stderr.strip()}")
        status, final_url = response.stdout.split("\t", 1)
        if expected is None:
            if status not in ("403", "404"):
                raise RuntimeError(f"Unexpected public response for {path}: {status}")
        else:
            if status != "200" or output.read_bytes() != (BUILD / expected).read_bytes():
                raise RuntimeError(f"Deployment response differs from build: {path} ({status})")
        return {"path": path, "status": int(status), "url": final_url}


def main():
    manifest = json.loads((BUILD.parent / "hs-rule-ui-manifest.json").read_text())
    cases = dict(ROUTES)
    for item in manifest:
        name = item["path"]
        if name.startswith("_"):
            continue
        if hashlib.sha256((BUILD / name).read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Build manifest mismatch: {name}")
        cases["/" + name] = name
    cases.update({path: None for path in PRIVATE_PATHS})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, cases.items()))
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"PASS: {len(results)} HTTPS routes/assets/publish-boundary checks")


if __name__ == "__main__":
    main()
