"""Release lifecycle on a server: activate, roll back, recover, prune, plan an upgrade.

A release is an immutable directory ``/opt/atlas/releases/<version>-<commit12>`` built by
``deploy/scripts/build-release.sh`` and described by its ``release.json``. The service
runs whichever release ``/opt/atlas/current`` points to. Activating a release:

1. takes the deployment lock (one deployment at a time);
2. refuses the release unless its files match its manifest (research artifacts, lock,
   contract), every file is owned by root and writable by nobody else, the deployment
   configuration is valid and every secret the profile needs is in place;
3. renders the systemd units, runtime environment and nginx site from the release's own
   templates, and checks nginx's configuration before using it;
4. records the activation as in progress, switches the link atomically, restarts the
   service (which reports ready only after verifying its data) and reloads nginx;
5. verifies the running deployment (:mod:`atlas.deploy.verify`);
6. on any failure, switches back to the previous release, renders its files again,
   restarts and verifies it. A first deployment that fails is stopped instead.

Nothing here deletes or edits a release: a failed deployment cannot alter the research
artifacts of any release. Each step is appended to ``/var/lib/atlas/deploy/history.jsonl``;
an activation interrupted half-way (a lost SSH session) leaves an ``in_progress`` record
that :func:`recover` completes. Every host action goes through :class:`Host`, so the tests
replace systemd and nginx with fakes.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

from atlas.deploy.credentials import SecretStore
from atlas.deploy.layout import (
    MONITOR_TIMER,
    RELEASE_MANIFEST,
    SERVICE,
    Layout,
    valid_release_id,
)
from atlas.deploy.manifest import ManifestError, check_release, load_manifest
from atlas.deploy.permissions import check_not_writable_by_others, check_release_tree
from atlas.deploy.render import Rendered, Renderer, disable_site, enable_site, write_files
from atlas.deploy.siteconfig import DeployConfig, DeployConfigError, load_config
from atlas.deploy.verify import Target, Verification

ACTIVATED: Final = "activated"
REFUSED: Final = "refused"
ROLLED_BACK: Final = "rolled_back"
ROLLBACK_FAILED: Final = "rollback_failed"
STOPPED: Final = "failed_and_stopped"
IN_PROGRESS: Final = "in_progress"
TERMINAL: Final = (ACTIVATED, REFUSED, ROLLED_BACK, ROLLBACK_FAILED, STOPPED)


class DeployError(RuntimeError):
    """A deployment step cannot proceed (another deployment, a missing release, ...)."""


class NotSwitched(DeployError):
    """The release's files were refused (by nginx or systemd) before anything switched."""


class Host(Protocol):
    """The host actions a deployment needs (systemd and nginx)."""

    def daemon_reload(self) -> tuple[bool, str]: ...
    def enable(self, *units: str) -> tuple[bool, str]: ...
    def reset_failed(self, unit: str) -> tuple[bool, str]: ...
    def restart(self, unit: str) -> tuple[bool, str]: ...
    def stop(self, unit: str) -> tuple[bool, str]: ...
    def main_pid(self, unit: str) -> int | None: ...
    def show(self, unit: str, properties: Sequence[str]) -> dict[str, str]: ...
    def nginx_test(self) -> tuple[bool, str]: ...
    def nginx_reload(self) -> tuple[bool, str]: ...
    def nginx_reload_if_running(self) -> tuple[bool, str]: ...


def _run(command: Sequence[str], timeout: float) -> tuple[bool, str]:
    executable = shutil.which(command[0])
    if executable is None:
        return False, f"{command[0]} not found"
    try:
        completed = subprocess.run(  # noqa: S603 - fixed systemctl/nginx commands
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, type(exc).__name__
    return completed.returncode == 0, (completed.stdout + completed.stderr).strip()[-2000:]


class SystemHost:
    """The real host: ``systemctl`` and ``nginx``."""

    def daemon_reload(self) -> tuple[bool, str]:
        return _run(["systemctl", "daemon-reload"], 60)

    def enable(self, *units: str) -> tuple[bool, str]:
        return _run(["systemctl", "enable", *units], 60)

    def reset_failed(self, unit: str) -> tuple[bool, str]:
        return _run(["systemctl", "reset-failed", unit], 30)

    def restart(self, unit: str) -> tuple[bool, str]:
        # Blocks until the unit reports ready (Type=notify) or fails (TimeoutStartSec).
        return _run(["systemctl", "restart", unit], 200)

    def stop(self, unit: str) -> tuple[bool, str]:
        return _run(["systemctl", "stop", unit], 120)

    def show(self, unit: str, properties: Sequence[str]) -> dict[str, str]:
        ok, output = _run(["systemctl", "show", unit, "-p", ",".join(properties)], 30)
        values: dict[str, str] = {}
        if ok:
            for line in output.splitlines():
                key, _, value = line.partition("=")
                values[key] = value
        return values

    def main_pid(self, unit: str) -> int | None:
        value = self.show(unit, ["MainPID"]).get("MainPID", "0")
        return int(value) if value.isdigit() and int(value) > 0 else None

    def nginx_test(self) -> tuple[bool, str]:
        return _run(["nginx", "-t", "-q"], 60)

    def nginx_reload(self) -> tuple[bool, str]:
        return _run(["systemctl", "reload", "nginx"], 60)

    def nginx_reload_if_running(self) -> tuple[bool, str]:
        return _run(["systemctl", "try-reload-or-restart", "nginx"], 60)


Verifier = Callable[[Target], Verification]


@dataclass
class Deployment:
    """One server's deployment state and the means to change it."""

    layout: Layout
    host: Host
    verifier: Verifier
    owner: int = 0
    proxy_gid: int = 33
    service_gid: int = 0
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    chown: bool = True

    # --- State -------------------------------------------------------------------------------

    def release_path(self, release_id: str) -> Path:
        return self.layout.local(self.layout.release(release_id))

    def current(self) -> str | None:
        link = self.layout.local(self.layout.current)
        if not link.is_symlink():
            return None
        name = link.readlink().name
        return name if valid_release_id(name) else None

    def releases(self) -> list[str]:
        """Complete releases (with a manifest), oldest first by build time."""
        base = self.layout.local(self.layout.releases)
        found: list[tuple[str, str]] = []
        if base.is_dir():
            for entry in base.iterdir():
                if valid_release_id(entry.name) and (entry / RELEASE_MANIFEST).is_file():
                    try:
                        built = str(load_manifest(entry).get("build", {}).get("built_at", ""))
                    except ManifestError:
                        built = ""
                    found.append((built, entry.name))
        return [name for _, name in sorted(found)]

    def history(self) -> list[dict[str, Any]]:
        path = self.layout.local(self.layout.history)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        records = []
        for line in lines:
            with contextlib.suppress(ValueError):
                records.append(json.loads(line))
        return records

    def record(self, action: str, outcome: str, **details: Any) -> dict[str, Any]:
        entry = {
            "ts": self.now().isoformat(timespec="seconds"),
            "action": action,
            "outcome": outcome,
            **details,
        }
        path = self.layout.local(self.layout.history)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def last_good(self, exclude: str | None = None) -> str | None:
        """The most recently activated release (other than ``exclude``) still on disk."""
        on_disk = set(self.releases())
        for entry in reversed(self.history()):
            release = entry.get("release")
            if entry.get("outcome") == ACTIVATED and release != exclude and release in on_disk:
                return str(release)
            restored = entry.get("restored")
            if entry.get("outcome") == ROLLED_BACK and restored != exclude and restored in on_disk:
                return str(restored)
        return None

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        path = self.layout.local(self.layout.lock)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DeployError(
                    "another deployment holds the lock; wait for it to finish"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # --- Checks ------------------------------------------------------------------------------

    def config(self) -> DeployConfig:
        path = self.layout.local(self.layout.deploy_env)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise DeployConfigError([f"{self.layout.deploy_env} is missing"]) from exc
        return load_config(text, self.layout)

    def secrets(self) -> SecretStore:
        return SecretStore(
            self.layout, owner=self.owner, owner_gid=self.owner_gid, proxy_gid=self.proxy_gid
        )

    @property
    def owner_gid(self) -> int:
        return 0 if self.owner == 0 else os.getegid()

    def check(self, release_id: str) -> tuple[list[str], DeployConfig | None]:
        """Everything that must hold before ``release_id`` may serve (empty: it may)."""
        if not valid_release_id(release_id):
            return [f"invalid release id {release_id!r}"], None
        release = self.release_path(release_id)
        if not release.is_dir():
            return [f"release {release_id} is not in {self.layout.releases}"], None
        problems = check_release(release)
        problems += [str(p) for p in check_release_tree(release, owner=self.owner)]
        config: DeployConfig | None = None
        try:
            config = self.config()
        except DeployConfigError as exc:
            problems += [f"deploy.env: {p}" for p in exc.problems]
        problems += [
            str(p)
            for p in check_not_writable_by_others(
                self.layout.local(self.layout.deploy_env),
                owner=self.owner,
                shown=str(self.layout.deploy_env),
            )
        ]
        if config is not None:
            problems += [
                str(p)
                for p in self.secrets().check(
                    proxy_key=config.profile == "private", proxy_users=config.profile == "private"
                )
            ]
            if config.exposed and config.tls_cert and config.tls_key:
                for path in (config.tls_cert, config.tls_key):
                    if not self.layout.local(path).exists():
                        problems.append(f"{path} is missing (obtain the certificate first)")
        return problems, config

    # --- Rendering ----------------------------------------------------------------------------

    def rendered(self, release_id: str, config: DeployConfig, stage: str = "tls") -> list[Rendered]:
        renderer = Renderer.for_release(config, self.layout, self.release_path(release_id))
        return renderer.files(stage)

    def install(self, release_id: str, config: DeployConfig) -> tuple[bool, str]:
        """Write the release's rendered files, check them with nginx and reload systemd.

        Nothing is switched or restarted here: if nginx refuses the site or systemd cannot
        reload, the previous files are put back and the running service is untouched.
        """
        files = self.rendered(release_id, config)
        backups = {item.path: self._read(item.path) for item in files}
        site = self._site_state()
        groups = {"root": 0, "atlas": self.service_gid, "www-data": self.proxy_gid}
        try:
            results = write_files(files, self.layout, chown=self.chown, group_ids=groups)
        except OSError as exc:  # a full disk, a missing group: put back what was written
            self._restore(files, backups, groups)
            return False, f"the rendered files could not be written: {exc}"
        changed = [str(r.path) for r in results if r.changed]
        units_changed = any(path.startswith(str(self.layout.systemd)) for path in changed)
        if config.exposed:
            enable_site(self.layout)
            ok, output = self.host.nginx_test()
            if not ok:
                self._restore(files, backups, groups, site)
                return False, f"nginx refused the rendered site: {output}"
        if units_changed:
            ok, output = self.host.daemon_reload()
            if not ok:
                self._restore(files, backups, groups, site)
                self.host.daemon_reload()
                return False, f"systemctl daemon-reload failed: {output}"
        if not config.exposed and site != (None, None):
            # The profile no longer exposes the API: an earlier profile's site must stop
            # serving now, not linger in nginx (removed, checked, reloaded if running).
            disable_site(self.layout)
            ok, output = self.host.nginx_test()
            if ok:
                ok, output = self.host.nginx_reload_if_running()
            if not ok:
                self._restore(files, backups, groups, site)
                if units_changed:
                    self.host.daemon_reload()
                return False, f"nginx refused the configuration without the ATLAS site: {output}"
            changed.append(f"{self.layout.nginx_site} (removed: the profile is not exposed)")
        self.host.enable(SERVICE, MONITOR_TIMER)
        return True, "changed: " + (", ".join(changed) or "nothing")

    def _site_state(self) -> tuple[bytes | None, str | None]:
        """The nginx site's file and its ``sites-enabled`` link, to put back on failure."""
        link = self.layout.local(self.layout.nginx_site_enabled)
        return self._read(self.layout.nginx_site), (
            str(link.readlink()) if link.is_symlink() else None
        )

    def _read(self, host_path: Any) -> bytes | None:
        try:
            return self.layout.local(host_path).read_bytes()
        except OSError:
            return None

    def _restore(
        self,
        files: list[Rendered],
        backups: Mapping[Any, bytes | None],
        groups: Mapping[str, int],
        site: tuple[bytes | None, str | None] = (None, None),
    ) -> None:
        site_file, site_link = site
        if site_file is not None:  # an earlier site taken out (or replaced): it comes back
            backups = {**backups, self.layout.nginx_site: site_file}
            if all(item.path != self.layout.nginx_site for item in files):
                files = [*files, Rendered(self.layout.nginx_site, "", 0o644, "root")]
        restore = [
            Rendered(item.path, (backups[item.path] or b"").decode("utf-8"), item.mode, item.group)
            for item in files
            if backups.get(item.path) is not None
        ]
        write_files(restore, self.layout, chown=self.chown, group_ids=groups)
        for item in files:
            if backups.get(item.path) is None:
                self.layout.local(item.path).unlink(missing_ok=True)
        link = self.layout.local(self.layout.nginx_site_enabled)
        if site_link is None:  # no link before: none now (it would point at nothing)
            if link.is_symlink():
                link.unlink()
        elif not link.is_symlink() or str(link.readlink()) != site_link:
            link.unlink(missing_ok=True)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(site_link)

    def switch(self, release_id: str) -> None:
        """Point ``current`` at ``release_id`` atomically (a new link renamed over it)."""
        link = self.layout.local(self.layout.current)
        temporary = link.with_name(".current.new")
        temporary.unlink(missing_ok=True)
        # Relative (releases/<id>): valid on the server and under a rehearsal root alike.
        temporary.symlink_to(str(self.layout.release(release_id).relative_to(self.layout.install)))
        temporary.replace(link)

    def target(self, release_id: str, config: DeployConfig) -> Target:
        manifest = load_manifest(self.release_path(release_id))
        key = None
        with contextlib.suppress(Exception):
            key = self.secrets().read_key()
        return Target(
            base_url=f"http://127.0.0.1:{config.api_port}",
            manifest=manifest,
            api_key=key,
            workers=config.resources.workers,
            release_dir=self.release_path(release_id),
            main_pid=self.host.main_pid(SERVICE),
            api_port=config.api_port,
        )

    def _start(self, release_id: str, config: DeployConfig) -> tuple[bool, list[str]]:
        """Install the release's files, switch to it, restart, reload nginx and verify.

        Raises :class:`NotSwitched` when anything fails before the switch (the running
        release is untouched). After the switch every failure, an unexpected exception
        included, is returned, so the caller restores the previous release.
        """
        try:
            ok, detail = self.install(release_id, config)
        except Exception as exc:  # rendering or a host command failed before the switch
            raise NotSwitched(f"installing the files failed: {type(exc).__name__}: {exc}") from exc
        if not ok:
            raise NotSwitched(detail)
        self.switch(release_id)
        try:
            return self._serve(release_id, config)
        except Exception as exc:  # never leave a switched release without a verdict
            return False, [f"unexpected error after the switch: {type(exc).__name__}: {exc}"]

    def _serve(self, release_id: str, config: DeployConfig) -> tuple[bool, list[str]]:
        """Restart the service on the switched release, reload nginx, verify."""
        # A deliberate restart by the operator: clear the unit's failure state and start
        # counter first, so earlier failures (or several activations and rollbacks in a
        # few minutes) cannot block it with "start-limit-hit". Automatic restarts keep
        # their limit (StartLimitBurst in the unit).
        self.host.reset_failed(SERVICE)
        ok, detail = self.host.restart(SERVICE)
        if not ok:
            return False, [f"systemctl restart {SERVICE} failed: {detail}"]
        if config.exposed:
            ok, detail = self.host.nginx_reload()
            if not ok:
                return False, [f"nginx reload failed: {detail}"]
        verification = self.verifier(self.target(release_id, config))
        if not verification.ok:
            return False, [f"{c.name}: {c.detail}" for c in verification.failures]
        return True, [f"{len(verification.checks)} checks passed"]

    # --- Operations ----------------------------------------------------------------------------

    def activate(self, release_id: str, *, action: str = "activate") -> dict[str, Any]:
        """Activate ``release_id``; on failure restore the previous release (module docstring)."""
        with self.locked():
            problems, config = self.check(release_id)
            previous = self.current()
            if problems or config is None:
                return self.record(
                    action, REFUSED, release=release_id, previous=previous, detail=problems[:30]
                )
            self.record(action, IN_PROGRESS, release=release_id, previous=previous)
            try:
                ok, detail = self._start(release_id, config)
            except NotSwitched as exc:
                # Refused before the switch: the running release and its files are as they were.
                return self.record(
                    action, REFUSED, release=release_id, previous=previous, detail=[str(exc)]
                )
            if ok:
                return self.record(
                    action, ACTIVATED, release=release_id, previous=previous, detail=detail
                )
            return self._fail(action, release_id, previous, detail, config)

    def _fail(
        self,
        action: str,
        release_id: str,
        previous: str | None,
        detail: list[str],
        config: DeployConfig,
    ) -> dict[str, Any]:
        restore = (
            previous
            if previous is not None and previous != release_id
            else self.last_good(exclude=release_id)
        )
        if restore is None:
            self.host.stop(SERVICE)
            return self.record(
                action, STOPPED, release=release_id, previous=previous, detail=detail
            )
        restore_problems, _ = self.check(restore)
        if restore_problems:
            self.host.stop(SERVICE)
            return self.record(
                action,
                ROLLBACK_FAILED,
                release=release_id,
                previous=previous,
                restored=restore,
                detail=detail + [f"cannot restore {restore}: {p}" for p in restore_problems[:10]],
            )
        try:
            ok, restore_detail = self._start(restore, config)
        except NotSwitched as exc:
            ok, restore_detail = False, [str(exc)]
        outcome = ROLLED_BACK if ok else ROLLBACK_FAILED
        return self.record(
            action,
            outcome,
            release=release_id,
            previous=previous,
            restored=restore,
            detail=detail + [f"restore of {restore}: {d}" for d in restore_detail],
        )

    def rollback(self, to: str | None = None) -> dict[str, Any]:
        """Activate ``to``, or the last release that was active before the current one."""
        current = self.current()
        target = to or self.last_good(exclude=current)
        if target is None:
            raise DeployError("no previous release to roll back to")
        return self.activate(target, action="rollback")

    def pending(self) -> dict[str, Any] | None:
        """The activation left in progress, if the last record of the history is one."""
        records = self.history()
        if records and records[-1].get("outcome") == IN_PROGRESS:
            return records[-1]
        return None

    def recover(self) -> dict[str, Any]:
        """Complete an interrupted activation: keep the current release if it verifies,
        otherwise restore the release that was active before it."""
        entry = self.pending()
        if entry is None:
            return {"outcome": "nothing_to_recover"}
        release = str(entry.get("release"))
        with self.locked():
            problems, config = self.check(release)
            if config is not None and not problems and self.current() == release:
                verification = self.verifier(self.target(release, config))
                if verification.ok:
                    return self.record(
                        "recover",
                        ACTIVATED,
                        release=release,
                        previous=entry.get("previous"),
                        detail=["verified after an interrupted activation"],
                    )
            if config is None:
                return self.record("recover", REFUSED, release=release, detail=problems[:30])
            previous = entry.get("previous")
            return self._fail(
                "recover",
                release,
                previous if isinstance(previous, str) else None,
                problems or ["the interrupted release did not verify"],
                config,
            )

    def prune(self, keep: int = 3) -> list[str]:
        """Delete old releases, keeping ``keep`` (at least 2) complete releases, always
        including the current one and the rollback target; incomplete releases (no
        manifest; a build that failed) are deleted unless current."""
        keep = max(2, keep)
        with self.locked():
            current = self.current()
            protected = {current, self.last_good(exclude=current)}
            complete = self.releases()
            kept = set(complete[-keep:]) | {p for p in protected if p}
            removed: list[str] = []
            base = self.layout.local(self.layout.releases)
            for entry in sorted(base.iterdir()) if base.is_dir() else []:
                name = entry.name
                if name in kept or name == current:
                    continue
                if valid_release_id(name) or name.startswith(".build-"):
                    _remove_tree(entry)
                    removed.append(name)
            if removed:
                self.record("prune", "pruned", removed=removed, kept=sorted(k for k in kept if k))
            return removed

    def status(self) -> dict[str, Any]:
        current = self.current()
        manifest: dict[str, Any] | None = None
        if current is not None:
            with contextlib.suppress(ManifestError):
                manifest = load_manifest(self.release_path(current))
        service = self.host.show(
            SERVICE,
            [
                "ActiveState",
                "SubState",
                "NRestarts",
                "MainPID",
                "ExecMainStartTimestamp",
                "Result",
                "StatusText",
            ],
        )
        return {
            "current": current,
            "rollback_target": self.last_good(exclude=current),
            "releases": self.releases(),
            "manifest": None
            if manifest is None
            else {
                key: manifest.get(key)
                for key in (
                    "release_id",
                    "git_commit",
                    "atlas_version",
                    "python",
                    "snapshot",
                    "api_contract_sha256",
                    "scientific_status",
                )
            }
            | {
                "lock_sha256": manifest.get("dependencies", {}).get("lock_sha256"),
                "research_fingerprint": manifest.get("research_artifacts", {}).get("fingerprint"),
            },
            "service": service,
            "pending_activation": self.pending(),
            "history": self.history()[-5:],
        }

    def plan(self, release_id: str) -> dict[str, Any]:
        """What activating ``release_id`` would change (read-only)."""
        problems, config = self.check(release_id)
        current = self.current()
        plan: dict[str, Any] = {
            "release": release_id,
            "current": current,
            "blocking_problems": problems,
        }
        try:
            candidate = load_manifest(self.release_path(release_id))
        except ManifestError as exc:
            plan["blocking_problems"] = [*problems, str(exc)]
            return plan
        old: dict[str, Any] = {}
        if current is not None:
            with contextlib.suppress(ManifestError):
                old = load_manifest(self.release_path(current))
        plan["changes"] = compare_manifests(old, candidate)
        if config is not None:
            files = self.rendered(release_id, config)
            plan["host_files_changing"] = [
                str(item.path)
                for item in files
                if self._read(item.path) != item.content.encode("utf-8")
            ]
        free = (
            shutil.disk_usage(self.layout.local(self.layout.install))
            if self.layout.local(self.layout.install).exists()
            else None
        )
        plan["free_disk_mib"] = None if free is None else free.free // (1 << 20)
        plan["commands"] = [
            f"atlas-deploy activate {release_id}"
            f"   # verifies, restores {current or 'nothing'} on failure",
            f"atlas-deploy rollback --to {current}" if current else "atlas-deploy status",
        ]
        return plan


def compare_manifests(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """The differences an operator must review before an upgrade."""
    old_packages: Mapping[str, str] = old.get("dependencies", {}).get("runtime_packages", {})
    new_packages: Mapping[str, str] = new.get("dependencies", {}).get("runtime_packages", {})
    old_files: Mapping[str, str] = old.get("research_artifacts", {}).get("files", {})
    new_files: Mapping[str, str] = new.get("research_artifacts", {}).get("files", {})
    return {
        "atlas_version": [old.get("atlas_version"), new.get("atlas_version")],
        "git_commit": [old.get("git_commit"), new.get("git_commit")],
        "python": [old.get("python", {}).get("version"), new.get("python", {}).get("version")],
        "lock_changed": old.get("dependencies", {}).get("lock_sha256")
        != new.get("dependencies", {}).get("lock_sha256"),
        "packages_changed": {
            name: [old_packages.get(name), new_packages.get(name)]
            for name in sorted(set(old_packages) | set(new_packages))
            if old_packages.get(name) != new_packages.get(name)
        },
        "api_contract_changed": old.get("api_contract_sha256") != new.get("api_contract_sha256"),
        "research_artifacts_changed": sorted(
            path
            for path in set(old_files) | set(new_files)
            if old_files.get(path) != new_files.get(path)
        ),
        "scientific_status": [old.get("scientific_status"), new.get("scientific_status")],
    }


def _remove_tree(path: Path) -> None:
    """Delete a (read-only) release tree."""

    def writable(function: Callable[..., Any], name: str, _: BaseException) -> None:
        with contextlib.suppress(OSError):
            Path(name).parent.chmod(stat.S_IRWXU)
        function(name)

    for directory, _, _ in os.walk(path):
        with contextlib.suppress(OSError):
            Path(directory).chmod(stat.S_IRWXU)
    shutil.rmtree(path, onexc=writable)
