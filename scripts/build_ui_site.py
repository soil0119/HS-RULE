"""Build the public UI folder without including repository or backend files."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "design/hs-rule-ui"
OUTPUT = ROOT / ".build/hs-rule-ui"
FILES = (
    "index.html", "login.html", "upload.html", "styles.css", "app.js", "api.js",
    "i18n.js", "icons.svg", "fonts/SUIT-Variable.woff2", "fonts/OFL-1.1.txt",
)
EXTRA = {
    "_redirects": "/login-page /login.html 200\n",
    "_headers": "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n",
}


def main():
    expected = set(FILES) | EXTRA.keys()
    if OUTPUT.exists():
        existing = {p.relative_to(OUTPUT).as_posix() for p in OUTPUT.rglob("*") if p.is_file()}
        if existing - expected:
            raise SystemExit("Unexpected files in build folder; inspect before publishing.")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = OUTPUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)
    for name, content in EXTRA.items():
        (OUTPUT / name).write_text(content, encoding="utf-8")

    actual = {p.relative_to(OUTPUT).as_posix() for p in OUTPUT.rglob("*") if p.is_file()}
    if actual != expected:
        raise SystemExit("Public build file list does not match the runtime allowlist.")
    manifest = [
        {"path": name, "bytes": (OUTPUT / name).stat().st_size,
         "sha256": hashlib.sha256((OUTPUT / name).read_bytes()).hexdigest()}
        for name in sorted(actual)
    ]
    (OUTPUT.parent / "hs-rule-ui-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{OUTPUT}\n{len(manifest)} public files; manifest stored outside the public folder.")


if __name__ == "__main__":
    main()
