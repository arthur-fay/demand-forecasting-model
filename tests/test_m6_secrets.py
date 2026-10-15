"""Secrets and permissions: the simulation key, proxy users, file modes and release trees.

The key is generated, never printed, stored root-only and kept identical in its systemd
credential and nginx's copy; passwords are hashed by openssl from standard input; weak
hashes, open modes, links and writable release files are refused.
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest

from atlas.deploy import cli
from atlas.deploy.credentials import (
    MIN_PASSWORD_LENGTH,
    SecretError,
    SecretStore,
    generate_key,
    generate_password,
    nginx_key_line,
    openssl_hash,
)
from atlas.deploy.layout import Layout
from atlas.deploy.permissions import (
    check_htpasswd,
    check_not_writable_by_others,
    check_private_dir,
    check_release_tree,
    check_secret_file,
)
from tests.m6_helpers import DEPLOY_ENV_PRIVATE, need_tools, run, write

UID, GID = os.geteuid(), os.getegid()


def store(tmp_path: Path, hasher: object = None) -> SecretStore:
    return SecretStore(
        Layout(root=tmp_path),
        owner=UID,
        owner_gid=GID,
        proxy_gid=GID,
        hasher=hasher or (lambda password: "$6$saltsalt$" + "x" * 86),  # type: ignore[arg-type]
    )


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_key_is_generated_once_and_shared_with_nginx(tmp_path: Path) -> None:
    secrets = store(tmp_path)
    assert "created" in secrets.init()[0]
    key = secrets.read_key()
    assert len(key) == 64
    assert int(key, 16) >= 0
    assert mode(secrets.key_path) == 0o600
    assert mode(secrets.nginx_key_path) == 0o600
    assert mode(secrets.key_path.parent) == 0o700
    assert secrets.nginx_key_path.read_text() == f'proxy_set_header X-API-Key "{key}";\n'
    assert secrets.init() == ["the simulation key exists and nginx has the same key"]
    assert secrets.read_key() == key  # idempotent: never replaced by init
    assert secrets.check(proxy_key=True, proxy_users=False) == []


def test_a_diverging_nginx_copy_is_detected_and_repaired(tmp_path: Path) -> None:
    secrets = store(tmp_path)
    secrets.init()
    write(secrets.nginx_key_path, nginx_key_line(generate_key()), 0o600)
    problems = secrets.check(proxy_key=True, proxy_users=False)
    assert any("does not hold the service's key" in str(p) for p in problems)
    assert "rewrote" in secrets.init()[0]
    assert secrets.check(proxy_key=True, proxy_users=False) == []


def test_rotation_replaces_both_copies(tmp_path: Path) -> None:
    secrets = store(tmp_path)
    secrets.init()
    old = secrets.read_key()
    actions = secrets.rotate()
    assert secrets.read_key() != old
    assert "systemctl restart atlas" in actions[-1]
    assert secrets.check(proxy_key=True, proxy_users=False) == []


def test_open_modes_links_and_foreign_keys_are_refused(tmp_path: Path) -> None:
    secrets = store(tmp_path)
    secrets.init()
    secrets.key_path.chmod(0o644)
    assert any("mode 0644" in str(p) for p in secrets.check(proxy_key=False, proxy_users=False))
    secrets.key_path.chmod(0o600)
    write(secrets.key_path, "an operator-made key that is long enough\n", 0o600)
    assert any("generated key" in str(p) for p in secrets.check(proxy_key=False, proxy_users=False))
    target = write(tmp_path / "elsewhere", generate_key() + "\n", 0o600)
    secrets.key_path.unlink()
    secrets.key_path.symlink_to(target)
    assert any(
        "not a regular file" in str(p) for p in secrets.check(proxy_key=False, proxy_users=False)
    )
    secrets.key_path.unlink()
    assert any("missing" in str(p) for p in secrets.check(proxy_key=False, proxy_users=False))


def test_nginx_key_lines_accept_generated_keys_only() -> None:
    with pytest.raises(SecretError):
        nginx_key_line('abc"; deny all; "')
    assert nginx_key_line("a" * 64).startswith("proxy_set_header X-API-Key")


# --- Proxy users ------------------------------------------------------------------------------


def test_users_are_added_replaced_and_removed(tmp_path: Path) -> None:
    secrets = store(tmp_path)
    generated = secrets.add_user("alice")
    assert generated is not None
    assert len(generated) >= 32
    assert secrets.add_user("bob", "b" * MIN_PASSWORD_LENGTH) is None
    secrets.add_user("alice", "c" * 25)  # replaced, not duplicated
    lines = secrets.htpasswd_path.read_text().splitlines()
    assert [line.split(":")[0] for line in lines] == ["bob", "alice"]
    assert mode(secrets.htpasswd_path) == 0o640
    assert secrets.remove_user("bob") is True
    assert secrets.remove_user("bob") is False
    secrets.init()
    assert secrets.check(proxy_key=True, proxy_users=True) == []
    assert secrets.remove_user("alice") is True
    assert any("has no user" in str(p) for p in secrets.check(proxy_key=True, proxy_users=True))


@pytest.mark.parametrize(
    ("name", "password"), [("Alice", None), ("a:b", None), ("bob", "short-password")]
)
def test_bad_users_and_short_passwords_are_refused(
    tmp_path: Path, name: str, password: str | None
) -> None:
    with pytest.raises(SecretError):
        store(tmp_path).add_user(name, password)


def test_openssl_hashes_are_sha512_crypt_and_verify() -> None:
    need_tools("openssl")
    password = generate_password()
    hashed = openssl_hash(password)
    assert hashed.startswith("$6$")
    salt = hashed.split("$")[2]
    again = run(["openssl", "passwd", "-6", "-salt", salt, "-stdin"], input=password + "\n")
    assert again.stdout.strip() == hashed
    assert check_htpasswd(f"alice:{hashed}\n") == []


@pytest.mark.parametrize(
    "line",
    [
        "alice:$apr1$abcdefgh$0123456789012345678901",
        "alice:{SHA}W6ph5Mm5Pz8GgiULbPgzG37mj9g=",
        "alice:{PLAIN}hunter2",
        "alice:abJnggxhB/yWI",
        "alice:$1$abc$def",
        "Alice:$6$s$h",
        "no-colon",
    ],
)
def test_weak_or_malformed_password_entries_are_refused(line: str) -> None:
    assert check_htpasswd(line + "\n")


def test_secret_keeping_cli_never_prints_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    write(root / "etc/atlas/deploy.env", DEPLOY_ENV_PRIVATE)
    assert cli.main(["--root", str(root), "secrets", "init"]) == 0
    key = (root / "etc/atlas/credentials/api-key").read_text().strip()
    out = io.StringIO()
    assert cli.main(["--root", str(root), "secrets", "rotate"], stdout=out) == 0
    new_key = (root / "etc/atlas/credentials/api-key").read_text().strip()
    out2 = io.StringIO()
    assert (
        cli.main(["--root", str(root), "secrets", "add-user", "alice", "--generate"], stdout=out2)
        == 0
    )
    captured = capsys.readouterr()
    for text in (out.getvalue(), out2.getvalue(), captured.out, captured.err):
        assert key not in text
        assert new_key not in text
    assert "shown once" in out2.getvalue()  # the generated password, once
    assert cli.main(["--root", str(root), "secrets", "check"]) == 0


# --- Files and trees ----------------------------------------------------------------------------


def test_file_and_directory_checks(tmp_path: Path) -> None:
    secret = write(tmp_path / "secret", "x", 0o600)
    assert check_secret_file(secret, owner=UID) == []
    assert check_secret_file(secret, owner=UID + 1)  # another owner
    assert check_secret_file(secret, owner=UID, group=GID + 1)
    assert check_secret_file(tmp_path, owner=UID)  # a directory
    private = tmp_path / "private"
    private.mkdir(mode=0o750)
    assert check_private_dir(private, owner=UID)
    private.chmod(0o700)
    assert check_private_dir(private, owner=UID) == []
    assert check_private_dir(secret, owner=UID)
    assert check_private_dir(tmp_path / "missing", owner=UID)
    config = write(tmp_path / "deploy.env", "", 0o664)
    assert check_not_writable_by_others(config, owner=UID)
    config.chmod(0o644)
    assert check_not_writable_by_others(config, owner=UID) == []
    assert check_not_writable_by_others(tmp_path / "none", owner=UID)


def test_release_trees_must_be_writable_by_root_only(tmp_path: Path) -> None:
    release = tmp_path / "release"
    write(release / "src/atlas/__init__.py", "", 0o644)
    write(release / ".venv/bin/atlas", "", 0o755)
    (release / ".venv/bin/python").symlink_to("/usr/bin/python3.12")
    (release / ".venv/lib64").symlink_to("lib")
    release.chmod(0o755)
    assert check_release_tree(release, owner=UID) == []
    (release / "src/atlas/__init__.py").chmod(0o664)
    (release / ".venv/bin/atlas").chmod(0o4755)
    (release / "escape").symlink_to("/etc/shadow")
    (release / "dotdot").symlink_to("/usr/lib/../../etc/shadow")  # an allowed prefix, escaped
    problems = [str(p) for p in check_release_tree(release, owner=UID)]
    assert any("dotdot" in p and "outside the release" in p for p in problems)
    assert any("src/atlas/__init__.py" in p and "writable" in p for p in problems)
    assert any(".venv/bin/atlas" in p and "set-id" in p for p in problems)
    assert any("escape" in p and "outside the release" in p for p in problems)
    assert any("owned by uid" in str(p) for p in check_release_tree(release, owner=UID + 1))
