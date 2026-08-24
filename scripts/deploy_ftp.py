from __future__ import annotations

import os
import socket
import time
from ftplib import FTP, FTP_TLS, error_temp
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = ROOT / "app"
CONNECT_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (5, 10, 20)


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


def connect_ftp(host: str, username: str, password: str, use_tls: bool):
    """Connect/login with bounded retries for temporary network/FTP failures."""

    retryable = (TimeoutError, socket.timeout, ConnectionError, OSError, EOFError, error_temp)
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        client = None
        try:
            print(f"FTP connection attempt {attempt} of {CONNECT_ATTEMPTS}...")
            client = FTP_TLS(host, timeout=60) if use_tls else FTP(host, timeout=60)
            client.login(username, password)
            if use_tls:
                client.prot_p()
            print("FTP connection established.")
            return client
        except retryable as exc:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            if attempt >= CONNECT_ATTEMPTS:
                raise
            delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
            print(f"Temporary FTP connection failure: {exc}. Retrying in {delay}s...")
            time.sleep(delay)

    raise RuntimeError("Unable to establish FTP connection")


def main() -> None:
    host = env("FTP_HOST")
    username = env("FTP_USERNAME")
    password = env("FTP_PASSWORD")
    remote = env("FTP_REMOTE_DIR", "/")
    use_tls = os.environ.get("FTP_TLS", "true").lower() not in {"0", "false", "no"}
    client = connect_ftp(host, username, password, use_tls)
    try:
        ensure_directory(client, remote)
        upload_tree(client, LOCAL_DIR)
        try:
            client.quit()
        except Exception:
            client.close()
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
