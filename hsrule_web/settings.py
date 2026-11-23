from dataclasses import dataclass
from os import environ
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ORIGIN = "https://hs.officeworkkt.com"
PLACEHOLDERS = {"1234", "admin", "password", "changeme", "change-me", "secret",
                "secret-key", "your-secret-key", "your-password"}


def is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (not normalized or normalized in PLACEHOLDERS
            or normalized.startswith(("replace-with-", "your-", "changeme", "change-me")))


@dataclass(frozen=True)
class Settings:
    database_url: str = ""
    upload_dir: Path = BASE_DIR / "uploads"
    admin_id: str = ""
    admin_pw: str = ""
    secret_key: str = ""
    public_origin: str = DEFAULT_ORIGIN
    allowed_hosts: tuple[str, ...] | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        values = {**dotenv_values(BASE_DIR / ".env"), **environ}
        return cls(
            database_url=values.get("DATABASE_URL") or "",
            upload_dir=Path(values.get("UPLOAD_DIR") or BASE_DIR / "uploads"),
            admin_id=values.get("ADMIN_ID") or "",
            admin_pw=values.get("ADMIN_PW") or "",
            secret_key=values.get("SECRET_KEY") or "",
            public_origin=(values.get("PUBLIC_ORIGIN") or DEFAULT_ORIGIN).rstrip("/"),
        )

    @property
    def admin_enabled(self) -> bool:
        username = self.admin_id.strip().lower()
        return ((username == "admin" or not is_placeholder(username))
                and not is_placeholder(self.admin_pw) and len(self.secret_key.strip()) >= 32
                and not is_placeholder(self.secret_key))

    @property
    def origin(self) -> str:
        origin = self.public_origin.rstrip("/")
        try:
            parsed = urlsplit(origin)
            return origin if (parsed.scheme in ("http", "https") and parsed.hostname and "*" not in origin
                              and not parsed.username and not parsed.password and "@" not in parsed.netloc
                              and not parsed.path and not parsed.query and not parsed.fragment
                              and parsed.port != 0 and all(char.isprintable() and not char.isspace()
                                                          for char in origin)) else DEFAULT_ORIGIN
        except ValueError:
            return DEFAULT_ORIGIN

    @property
    def hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts is not None:
            return self.allowed_hosts
        public_host = urlsplit(self.origin).hostname
        return tuple(host for host in (public_host, "localhost", "127.0.0.1") if host)
