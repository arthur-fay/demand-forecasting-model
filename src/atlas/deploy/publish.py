"""Publishing a release's dashboard for nginx (M7).

A release is readable by the service group only (``root:atlas``, others nothing), so nginx
(``www-data``) cannot read the dashboard inside it, and it should not: giving the proxy
the service group would let it read the whole release. Instead every activation of an
exposed profile publishes a verified copy of the release's build:

    /opt/atlas/dashboard/                 root:www-data 0750
        <release id>/                     root:www-data 0750, files 0640, no links

The copy is made in a staging directory, every file checked against the build manifest,
then renamed into place: nginx never sees a partial copy. A copy that already exists is
kept only if it still matches the release's build byte for byte, with the expected owner,
group and modes; otherwise it is replaced. The rendered site points at the copy of its own
release, so a rollback (which renders the restored release's site) moves the dashboard and
the API together. Copies of releases that no longer exist are removed by ``prune``.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
from pathlib import Path, PurePosixPath
from secrets import token_hex
from typing import Final

from atlas.dashboard import DIST, MANIFEST_NAME, DashboardBuild, verify_build
from atlas.deploy.layout import Layout, valid_release_id

DIR_MODE: Final = 0o750
FILE_MODE: Final = 0o640


class PublishError(RuntimeError):
    """The dashboard could not be published (the activation is refused before switching)."""


def check_published(
    path: Path, build: DashboardBuild, *, owner: int, proxy_gid: int | None
) -> list[str]:
    """Differences between a published copy and ``build`` (empty: it can be served).

    The files must be exactly the build's (:func:`atlas.dashboard.verify_build`), and every
    entry owned by ``owner``, in the proxy group (when given), writable by nobody else and
    never a link.
    """
    try:
        info = path.lstat()
    except OSError:
        return [f"{path} is missing"]
    if not stat.S_ISDIR(info.st_mode):
        return [f"{path} is not a directory"]
    problems = verify_build(path, build)
    for entry in [path, *sorted(path.rglob("*"))]:
        meta = entry.lstat()
        shown = entry.relative_to(path).as_posix() if entry != path else "."
        if stat.S_ISLNK(meta.st_mode):
            problems.append(f"{shown} is a link")
            continue
        wanted = DIR_MODE if stat.S_ISDIR(meta.st_mode) else FILE_MODE
        if stat.S_IMODE(meta.st_mode) != wanted:
            problems.append(f"{shown} has mode {stat.S_IMODE(meta.st_mode):04o}, not {wanted:04o}")
        if meta.st_uid != owner:
            problems.append(f"{shown} is owned by uid {meta.st_uid}, not {owner}")
        if proxy_gid is not None and meta.st_gid != proxy_gid:
            problems.append(f"{shown} has group {meta.st_gid}, not the proxy group {proxy_gid}")
    return problems[:50]


def _set(path: Path, mode: int, *, chown: bool, proxy_gid: int) -> None:
    path.chmod(mode)
    if chown:
        os.chown(path, 0, proxy_gid)


def publish(
    layout: Layout,
    release_id: str,
    release_dir: Path,
    build: DashboardBuild,
    *,
    owner: int = 0,
    proxy_gid: int = 33,
    chown: bool = True,
) -> PurePosixPath:
    """Publish the release's verified dashboard for nginx; its host path."""
    if not valid_release_id(release_id):
        raise PublishError(f"invalid release id {release_id!r}")
    source = release_dir / DIST
    problems = verify_build(source, build)
    if problems:
        raise PublishError(
            "the release's dashboard does not match its build: " + "; ".join(problems[:5])
        )
    host = layout.dashboard(release_id)
    root = layout.local(layout.dashboard_root)
    target = layout.local(host)
    expected_gid = proxy_gid if chown else None
    if not check_published(target, build, owner=owner, proxy_gid=expected_gid):
        return host
    root.mkdir(parents=True, exist_ok=True)
    _set(root, DIR_MODE, chown=chown, proxy_gid=proxy_gid)
    staging = root / f".{release_id}.new-{token_hex(4)}"
    try:
        staging.mkdir(mode=DIR_MODE)
        _set(staging, DIR_MODE, chown=chown, proxy_gid=proxy_gid)
        for item in [*build.files, None]:
            name = MANIFEST_NAME if item is None else item.path
            data = (source / name).read_bytes()
            if item is not None and hashlib.sha256(data).hexdigest() != item.sha256:
                raise PublishError(f"dashboard file changed while publishing: {name}")
            destination = staging / name
            if destination.parent != staging and not destination.parent.exists():
                destination.parent.mkdir(mode=DIR_MODE)
                _set(destination.parent, DIR_MODE, chown=chown, proxy_gid=proxy_gid)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            _set(destination, FILE_MODE, chown=chown, proxy_gid=proxy_gid)
        problems = check_published(staging, build, owner=owner, proxy_gid=expected_gid)
        if problems:
            raise PublishError("the published copy does not verify: " + "; ".join(problems[:5]))
        if target.exists() or target.is_symlink():
            retired = root / f".{release_id}.old-{token_hex(4)}"
            target.rename(retired)
            staging.rename(target)
            _remove(retired)
        else:
            staging.rename(target)
    except OSError as exc:
        raise PublishError(f"the dashboard could not be published: {exc}") from exc
    finally:
        if staging.exists():
            _remove(staging)
    return host


def prune_published(layout: Layout, keep: set[str]) -> list[str]:
    """Remove published copies of releases not in ``keep``, and leftovers of interrupted
    publications; the names removed."""
    root = layout.local(layout.dashboard_root)
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for entry in sorted(root.iterdir()):
        name = entry.name
        stale = name.startswith(".") and (".new-" in name or ".old-" in name)
        if stale or (valid_release_id(name) and name not in keep):
            _remove(entry)
            removed.append(name)
    return removed


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    for directory, _, _ in os.walk(path):
        with contextlib.suppress(OSError):
            Path(directory).chmod(0o700)
    shutil.rmtree(path, ignore_errors=True)
