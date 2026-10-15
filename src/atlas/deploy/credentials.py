"""The secrets of a deployment (``atlas-deploy secrets``): generated on the server, never
printed, never committed, never placed in the environment.

* The **simulation key** protects ``POST /api/v1/simulations``. It lives in
  ``/etc/atlas/credentials/api-key`` (root, 0600); systemd hands the service a private copy
  (``LoadCredential``), and nginx adds it to the simulation requests of authenticated users
  from ``/etc/nginx/atlas/simulation-key.conf`` (root, 0600). Browsers never see it.
* **Proxy accounts** (private profile) are HTTP Basic users in
  ``/etc/nginx/atlas/htpasswd`` (root:www-data, 0640) with SHA-512-crypt hashes made by
  ``openssl passwd -6`` (the password goes through standard input, never the command
  line). nginx verifies the hash on every request, so a deliberately slow hash would let
  unauthenticated clients exhaust the proxy; the protection is the password's entropy
  instead: generated passwords have 192 bits, chosen ones need at least 20 characters.

Every file is written atomically (temporary file, fsync, rename) with its final owner and
mode before it becomes visible.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from atlas.api.settings import read_api_key_file
from atlas.deploy.layout import Layout
from atlas.deploy.permissions import (
    USER_NAME,
    Problem,
    check_htpasswd,
    check_private_dir,
    check_secret_file,
)
from atlas.errors import ConfigError

KEY_BYTES: Final = 32
PASSWORD_BYTES: Final = 24
MIN_PASSWORD_LENGTH: Final = 20
_KEY: Final = re.compile(r"[0-9a-f]{64}")
_NGINX_KEY_LINE: Final = re.compile(r'proxy_set_header X-API-Key "([0-9a-f]{64})";\n')

Hasher = Callable[[str], str]


class SecretError(ValueError):
    """A secret cannot be created, read or updated safely."""


def generate_key() -> str:
    return secrets.token_hex(KEY_BYTES)


def generate_password() -> str:
    return secrets.token_urlsafe(PASSWORD_BYTES)


def openssl_hash(password: str) -> str:
    """SHA-512-crypt of ``password`` (``$6$``), with the password on standard input."""
    executable = shutil.which("openssl")
    if executable is None:
        raise SecretError("openssl is required to hash passwords (apt install openssl)")
    completed = subprocess.run(  # noqa: S603 - fixed command; the password is on stdin
        [executable, "passwd", "-6", "-stdin"],
        input=password + "\n",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    hashed = completed.stdout.strip()
    if completed.returncode != 0 or not hashed.startswith("$6$"):
        raise SecretError("openssl could not hash the password")
    return hashed


def nginx_key_line(key: str) -> str:
    if not _KEY.fullmatch(key):
        raise SecretError("the simulation key must be 64 lowercase hexadecimal characters")
    return f'proxy_set_header X-API-Key "{key}";\n'


def write_atomic(path: Path, data: bytes, *, mode: int, uid: int, gid: int) -> None:
    """Write ``data`` to ``path`` atomically, with its owner and mode set before the rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        if (uid, gid) != (os.geteuid(), os.getegid()):
            os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).chmod(mode)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class SecretStore:
    """The secrets of one deployment. ``owner`` is root on a server; ``proxy_gid`` is the
    group nginx's workers run as (``www-data``), which must read the user file."""

    layout: Layout
    owner: int = 0
    owner_gid: int = 0
    proxy_gid: int = 33
    hasher: Hasher = openssl_hash

    @property
    def key_path(self) -> Path:
        return self.layout.local(self.layout.api_key)

    @property
    def nginx_key_path(self) -> Path:
        return self.layout.local(self.layout.nginx_key)

    @property
    def htpasswd_path(self) -> Path:
        return self.layout.local(self.layout.htpasswd)

    def _dirs(self) -> None:
        credentials = self.layout.local(self.layout.credentials)
        credentials.mkdir(parents=True, exist_ok=True)
        credentials.chmod(0o700)
        nginx = self.layout.local(self.layout.nginx)
        nginx.mkdir(parents=True, exist_ok=True)
        # nginx's workers (group www-data) read the user file at request time.
        nginx.chmod(0o750)
        if (self.owner, self.proxy_gid) != (os.geteuid(), os.getegid()):
            os.chown(credentials, self.owner, self.owner_gid)
            os.chown(nginx, self.owner, self.proxy_gid)

    def read_key(self) -> str:
        try:
            key = read_api_key_file(self.key_path)
        except ConfigError as exc:
            raise SecretError(str(exc)) from exc
        if not _KEY.fullmatch(key):
            raise SecretError(f"{self.layout.api_key} does not hold a generated key")
        return key

    def _write_key(self, key: str) -> None:
        self._dirs()
        write_atomic(
            self.key_path,
            (key + "\n").encode("ascii"),
            mode=0o600,
            uid=self.owner,
            gid=self.owner_gid,
        )
        write_atomic(
            self.nginx_key_path,
            nginx_key_line(key).encode("ascii"),
            mode=0o600,
            uid=self.owner,
            gid=self.owner_gid,
        )

    def init(self) -> list[str]:
        """Create the key if it does not exist, and make the nginx copy match it."""
        actions: list[str] = []
        if not self.key_path.exists():
            self._write_key(generate_key())
            return [f"created {self.layout.api_key} and {self.layout.nginx_key}"]
        key = self.read_key()
        if not self._nginx_matches(key):
            self._write_key(key)
            actions.append(f"rewrote {self.layout.nginx_key} to match the key")
        return actions or ["the simulation key exists and nginx has the same key"]

    def rotate(self) -> list[str]:
        """Replace the key (restart atlas and reload nginx afterwards)."""
        self._write_key(generate_key())
        return [
            f"replaced the key in {self.layout.api_key} and {self.layout.nginx_key}",
            "now run: systemctl restart atlas && systemctl reload nginx",
        ]

    def _nginx_matches(self, key: str) -> bool:
        try:
            text = self.nginx_key_path.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            return False
        match = _NGINX_KEY_LINE.fullmatch(text)
        return match is not None and hmac.compare_digest(match.group(1), key)

    # --- Proxy users -------------------------------------------------------------------------

    def _users(self) -> list[str]:
        try:
            return self.htpasswd_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

    def add_user(self, name: str, password: str | None = None) -> str | None:
        """Add or replace ``name``; returns the generated password when none was given
        (shown once to the operator, never stored in clear)."""
        if not USER_NAME.fullmatch(name):
            raise SecretError("user names are lowercase letters, digits, '.', '_' or '-'")
        generated = None
        if password is None:
            password = generated = generate_password()
        elif len(password) < MIN_PASSWORD_LENGTH:
            raise SecretError(
                f"passwords need at least {MIN_PASSWORD_LENGTH} characters (or use --generate)"
            )
        hashed = self.hasher(password)
        lines = [line for line in self._users() if line.split(":", 1)[0] != name]
        lines.append(f"{name}:{hashed}")
        self._dirs()
        write_atomic(
            self.htpasswd_path,
            ("\n".join(lines) + "\n").encode("utf-8"),
            mode=0o640,
            uid=self.owner,
            gid=self.proxy_gid,
        )
        return generated

    def remove_user(self, name: str) -> bool:
        lines = self._users()
        kept = [line for line in lines if line.split(":", 1)[0] != name]
        if len(kept) == len(lines):
            return False
        write_atomic(
            self.htpasswd_path,
            ("\n".join(kept) + "\n").encode("utf-8") if kept else b"",
            mode=0o640,
            uid=self.owner,
            gid=self.proxy_gid,
        )
        return True

    # --- Checks ----------------------------------------------------------------------------------

    def check(self, *, proxy_key: bool, proxy_users: bool) -> list[Problem]:
        """Every secret this profile needs, with owner, mode and content checked."""
        layout = self.layout
        problems = check_private_dir(
            layout.local(layout.credentials), owner=self.owner, shown=str(layout.credentials)
        )
        key_problems = check_secret_file(self.key_path, owner=self.owner, shown=str(layout.api_key))
        problems += key_problems
        key = None
        if not key_problems:
            try:
                key = self.read_key()
            except SecretError as exc:
                problems.append(Problem(str(layout.api_key), str(exc)))
        if proxy_key:
            nginx_problems = check_secret_file(
                self.nginx_key_path, owner=self.owner, modes=(0o600,), shown=str(layout.nginx_key)
            )
            problems += nginx_problems
            if not nginx_problems and key is not None and not self._nginx_matches(key):
                problems.append(
                    Problem(
                        str(layout.nginx_key),
                        "does not hold the service's key: simulations through nginx would be "
                        "refused",
                        "atlas-deploy secrets init",
                    )
                )
        if proxy_users:
            user_problems = check_secret_file(
                self.htpasswd_path,
                owner=self.owner,
                group=self.proxy_gid,
                modes=(0o640,),
                shown=str(layout.htpasswd),
            )
            problems += user_problems
            if not user_problems:
                problems += check_htpasswd(
                    self.htpasswd_path.read_text(encoding="utf-8"), str(layout.htpasswd)
                )
        return problems
