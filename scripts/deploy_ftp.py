from __future__ import annotations

import os
from ftplib import FTP, FTP_TLS
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = ROOT / "app"


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def ensure_directory(ftp, path: str) -> None:
    ftp.cwd("/")
    for part in [part for part in path.split("/") if part]:
        try:
            ftp.cwd(part)
        except Exception:
            ftp.mkd(part)
            ftp.cwd(part)


def upload_tree(ftp, local: Path) -> None:
    for item in sorted(local.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            try:
                ftp.mkd(item.name)
            except Exception:
                pass
            ftp.cwd(item.name)
            upload_tree(ftp, item)
            ftp.cwd("..")
        else:
            with item.open("rb") as handle:
                ftp.storbinary(f"STOR {item.name}", handle)
            print(f"Uploaded {item.relative_to(LOCAL_DIR)}")


def main() -> None:
    host = env("FTP_HOST")
    username = env("FTP_USERNAME")
    password = env("FTP_PASSWORD")
    remote = env("FTP_REMOTE_DIR", "/")
    use_tls = os.environ.get("FTP_TLS", "true").lower() not in {"0", "false", "no"}
    client = FTP_TLS(host, timeout=60) if use_tls else FTP(host, timeout=60)
    client.login(username, password)
    if use_tls:
        client.prot_p()
    ensure_directory(client, remote)
    upload_tree(client, LOCAL_DIR)
    client.quit()


if __name__ == "__main__":
    main()
