"""Ownership and permission checks of secrets, configuration and releases (M6).

The service account (``atlas``) must be able to read its release and nothing it could use
to escalate: it may not write any file it executes or that root executes, it never reads
the credential store directly (systemd hands it a copy), and other local users read no
secret. These checks refuse a deployment whose files break those rules, with the exact
path and the command that fixes it.

Every check takes the expected owner and groups as numbers, so the tests can run them on
files owned by an unprivileged user; on a server the owner is root (uid 0).
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

#: Hash schemes nginx's crypt(3) accepts that are acceptable for passwords.
STRONG_HASHES: Final = ("$2y$", "$2b$", "$y$", "$7$", "$6$", "$5$")
#: Recognised but refused: MD5-based, SHA-1, DES and plain text.
WEAK_HASHES: Final = ("$apr1$", "$1$", "{SHA}", "{SSHA}", "{PLAIN}")
USER_NAME: Final = re.compile(r"[a-z][a-z0-9._-]{0,31}")
#: Symbolic links in a release may point inside it, or to the system interpreter.
SYSTEM_TARGETS: Final = ("/usr/bin/", "/usr/lib/", "/usr/local/bin/", "/opt/atlas/tools/")


@dataclass(frozen=True)
class Problem:
    path: str
    message: str
    fix: str = ""

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" + (f" (fix: {self.fix})" if self.fix else "")


def _mode(info: os.stat_result) -> str:
    return f"{stat.S_IMODE(info.st_mode):04o}"


def check_secret_file(
    path: Path,
    *,
    owner: int,
    group: int | None = None,
    modes: Sequence[int] = (0o600, 0o400),
    shown: str | None = None,
) -> list[Problem]:
    """A secret: a regular file (not a link), owned by ``owner`` (and ``group``), with one
    of ``modes``."""
    name = shown or str(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return [Problem(name, "missing", "atlas-deploy secrets init")]
    except OSError as exc:
        return [Problem(name, f"cannot be inspected ({type(exc).__name__})")]
    problems: list[Problem] = []
    if not stat.S_ISREG(info.st_mode):
        problems.append(Problem(name, "is not a regular file (links are refused)"))
        return problems
    if info.st_uid != owner:
        problems.append(Problem(name, f"is owned by uid {info.st_uid}", f"chown {owner} {name}"))
    if group is not None and info.st_gid != group:
        problems.append(Problem(name, f"has group gid {info.st_gid}", f"chgrp {group} {name}"))
    if stat.S_IMODE(info.st_mode) not in modes:
        wanted = " or ".join(f"{m:04o}" for m in modes)
        problems.append(
            Problem(
                name, f"has mode {_mode(info)}, expected {wanted}", f"chmod {modes[0]:o} {name}"
            )
        )
    return problems


def check_private_dir(path: Path, *, owner: int, shown: str | None = None) -> list[Problem]:
    """A directory of secrets: owned by ``owner``, no access for group or others."""
    name = shown or str(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return [Problem(name, "missing", "atlas-deploy secrets init")]
    if not stat.S_ISDIR(info.st_mode):
        return [Problem(name, "is not a directory")]
    problems = []
    if info.st_uid != owner:
        problems.append(Problem(name, f"is owned by uid {info.st_uid}", f"chown {owner} {name}"))
    if info.st_mode & 0o077:
        problems.append(Problem(name, f"has mode {_mode(info)}", f"chmod 700 {name}"))
    return problems


def check_not_writable_by_others(
    path: Path, *, owner: int, shown: str | None = None
) -> list[Problem]:
    """A file root reads as configuration: owned by ``owner``, writable by nobody else."""
    name = shown or str(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return [Problem(name, "missing")]
    problems = []
    if stat.S_ISLNK(info.st_mode):
        problems.append(Problem(name, "is a symbolic link (refused for configuration)"))
    if info.st_uid != owner:
        problems.append(Problem(name, f"is owned by uid {info.st_uid}", f"chown {owner} {name}"))
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        problems.append(Problem(name, f"has mode {_mode(info)}", f"chmod go-w {name}"))
    return problems


def check_htpasswd(text: str, shown: str = "htpasswd") -> list[Problem]:
    """Every line ``user:hash`` with a strong hash; at least one user."""
    problems: list[Problem] = []
    users = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        user, sep, hashed = line.partition(":")
        where = f"{shown}:{number}"
        if not sep or not USER_NAME.fullmatch(user):
            problems.append(Problem(where, "expected user:hash with a lowercase user name"))
            continue
        if hashed.startswith(WEAK_HASHES) or not hashed.startswith(STRONG_HASHES):
            problems.append(
                Problem(
                    where,
                    f"user {user!r} has a weak or plain password hash",
                    f"atlas-deploy secrets add-user {user} --generate",
                )
            )
            continue
        users += 1
    if users == 0 and not problems:
        problems.append(Problem(shown, "has no user", "atlas-deploy secrets add-user NAME"))
    return problems


def check_release_tree(
    release: Path, *, owner: int, allowed_targets: Iterable[str] = SYSTEM_TARGETS
) -> list[Problem]:
    """Every entry of a release is owned by ``owner``, writable by nobody else, without
    set-id bits, and its links stay inside the release (or point to the system Python).

    The service runs code from here and root runs the deployment tool from here: a file
    the service account could change would be a way to escalate to root.
    """
    problems: list[Problem] = []
    targets = tuple(allowed_targets)
    resolved_release = release.resolve()
    for directory, names, files in os.walk(release, followlinks=False):
        base = Path(directory)
        for name in [*names, *files, *([] if base != release else [""])]:
            path = base / name if name else base
            try:
                info = path.lstat()
            except OSError as exc:
                problems.append(Problem(str(path), f"cannot be inspected ({type(exc).__name__})"))
                continue
            relative = str(path.relative_to(release)) or "."
            if info.st_uid != owner:
                problems.append(Problem(relative, f"is owned by uid {info.st_uid}, not {owner}"))
            if stat.S_ISLNK(info.st_mode):
                target = str(path.readlink())
                absolute = (
                    (path.parent / target).resolve() if not target.startswith("/") else Path(target)
                )
                inside = absolute == resolved_release or resolved_release in absolute.parents
                if not inside and not str(absolute).startswith(targets):
                    problems.append(Problem(relative, f"links outside the release to {target}"))
                continue
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                problems.append(
                    Problem(relative, f"is writable by group or others ({_mode(info)})")
                )
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                problems.append(Problem(relative, f"has a set-id bit ({_mode(info)})"))
        if len(problems) > 50:
            problems.append(Problem(str(release), "more problems not listed"))
            break
    return problems
