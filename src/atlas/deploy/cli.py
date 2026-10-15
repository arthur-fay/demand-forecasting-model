"""``atlas-deploy``: the deployment command (standard library only).

Exit codes: 0 = done or passed, 1 = a check or a deployment failed (the reason is
printed), 2 = usage or configuration error, 3 = unexpected internal error.

Read-only commands: ``preflight``, ``validate``, ``render`` (without ``--apply``),
``manifest show|check``, ``verify``, ``status``, ``plan-upgrade``, ``monitor``,
``secrets check``. Commands that change the server (``activate``, ``rollback``,
``recover``, ``prune``, ``render --apply``, ``secrets init|rotate|add-user|remove-user``)
must run as root, except under ``--root`` (a scratch directory standing for ``/``, for
rehearsals and tests). Secrets are never printed, except a generated password, once.
"""

from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
import sys
import traceback
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from atlas.deploy import monitor as monitoring
from atlas.deploy import preflight
from atlas.deploy.credentials import SecretError, SecretStore
from atlas.deploy.layout import PROXY_GROUP, SERVICE_GROUP, Layout
from atlas.deploy.manifest import (
    ManifestError,
    build_manifest,
    check_release,
    load_manifest,
    write_manifest,
)
from atlas.deploy.releases import DeployError, Deployment, SystemHost
from atlas.deploy.render import STAGES, Renderer, RenderError, enable_site, write_files
from atlas.deploy.routes import RouteError
from atlas.deploy.siteconfig import (
    DeployConfig,
    DeployConfigError,
    load_config,
    runtime_environment,
)
from atlas.deploy.verify import Target, verify

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 3


def default_release_dir() -> Path:
    """The release (or checkout) this tool belongs to: ``<dir>/src/atlas/deploy/cli.py``."""
    return Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas-deploy", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/"),
        help="Directory standing for / (rehearsals and tests; default: /).",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    pre = sub.add_parser("preflight", help="Read-only assessment of this server.")
    pre.add_argument("--resources", choices=sorted(preflight.PROFILES), default="standard")
    pre.add_argument("--release-dir", type=Path, default=None)
    pre.add_argument("--probe-dependencies", action="store_true")
    pre.add_argument("--offline", action="store_true")

    validate = sub.add_parser("validate", help="Validate deploy.env and what it renders.")
    validate.add_argument(
        "--env-file", type=Path, default=None, help="Default: /etc/atlas/deploy.env."
    )
    validate.add_argument("--release-dir", type=Path, default=None)
    validate.add_argument(
        "--installed", action="store_true", help="Also check secrets, certificates and permissions."
    )

    render = sub.add_parser("render", help="Render the units, environment and nginx site.")
    render.add_argument("--release-dir", type=Path, default=None)
    render.add_argument("--stage", choices=STAGES, default="tls")
    target = render.add_mutually_exclusive_group()
    target.add_argument("--output-dir", type=Path, default=None, help="Write under this directory.")
    target.add_argument("--apply", action="store_true", help="Install the files (root).")

    secrets = sub.add_parser("secrets", help="The simulation key and the proxy users.")
    secret_sub = secrets.add_subparsers(dest="secrets_command", required=True)
    secret_sub.add_parser("init", help="Create the key if missing; keep nginx's copy in sync.")
    secret_sub.add_parser("rotate", help="Replace the key (restart atlas, reload nginx after).")
    secret_sub.add_parser("check", help="Check every secret (owner, mode, content).")
    add = secret_sub.add_parser("add-user", help="Add or replace an HTTP Basic user (private).")
    add.add_argument("name")
    how = add.add_mutually_exclusive_group(required=True)
    how.add_argument("--generate", action="store_true", help="Generate a password (shown once).")
    how.add_argument("--password-stdin", action="store_true", help="Read the password from stdin.")
    remove = secret_sub.add_parser("remove-user", help="Remove an HTTP Basic user.")
    remove.add_argument("name")

    manifest = sub.add_parser("manifest", help="Release manifests.")
    manifest_sub = manifest.add_subparsers(dest="manifest_command", required=True)
    write = manifest_sub.add_parser("write", help="Write release.json (release builds).")
    write.add_argument("release_dir", type=Path)
    write.add_argument("--commit", required=True)
    write.add_argument("--uv-version", required=True)
    write.add_argument("--check", action="append", default=[], help="A check that passed.")
    for name in ("show", "check"):
        item = manifest_sub.add_parser(name, help=f"{name.capitalize()} a release's manifest.")
        item.add_argument("release_dir", type=Path)

    ver = sub.add_parser(
        "verify", help="Verify the running deployment (health, identity, integrity)."
    )
    ver.add_argument("--timeout", type=float, default=90.0)
    ver.add_argument("--no-simulation", action="store_true", help="Skip the smoke simulation.")

    act = sub.add_parser("activate", help="Activate a built release (root); restores on failure.")
    act.add_argument("release_id")
    back = sub.add_parser("rollback", help="Activate the previous release (root).")
    back.add_argument("--to", default=None, help="A specific release id.")
    sub.add_parser("recover", help="Complete an interrupted activation (root).")
    prune = sub.add_parser("prune", help="Delete old releases (root).")
    prune.add_argument("--keep", type=int, default=3)
    sub.add_parser("status", help="Current release, service state and recent history.")
    plan = sub.add_parser("plan-upgrade", help="What activating a release would change.")
    plan.add_argument("release_id")

    mon = sub.add_parser("monitor", help="One monitoring run (atlas-monitor.timer).")
    mon.add_argument("--api-port", type=int, default=8000)
    mon.add_argument("--state-dir", type=Path, default=None)
    mon.add_argument("--tls-domain", default=None)
    mon.add_argument("--tls-port", type=int, default=443)
    mon.add_argument("--tls-ca", default=None, help="CA bundle for the certificate check.")
    return parser


def _emit(out: TextIO, payload: Any, as_json: bool, text: str | None = None) -> None:
    if as_json or text is None:
        out.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    else:
        out.write(text if text.endswith("\n") else text + "\n")


def _gid(name: str) -> int:
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError as exc:
        raise DeployError(f"group {name!r} does not exist; run the bootstrap script") from exc


class Context:
    """The layout and identities of one invocation (a server, or a rehearsal root)."""

    def __init__(self, root: Path) -> None:
        self.layout = Layout(root=root)
        self.rehearsal = root.resolve() != Path("/")
        if self.rehearsal:
            self.owner, self.proxy_gid, self.service_gid = os.geteuid(), os.getegid(), os.getegid()
        else:
            self.owner = 0
            self.proxy_gid = -1
            self.service_gid = -1

    def ids(self) -> None:
        if not self.rehearsal:
            self.proxy_gid = _gid(PROXY_GROUP)
            self.service_gid = _gid(SERVICE_GROUP)

    def require_root(self) -> None:
        if not self.rehearsal and os.geteuid() != 0:
            raise DeployError("this command changes the server: run it as root (sudo)")
        self.ids()

    def deployment(self) -> Deployment:
        return Deployment(
            self.layout,
            SystemHost(),
            lambda target: verify(target),
            owner=self.owner,
            proxy_gid=self.proxy_gid,
            service_gid=self.service_gid,
            chown=not self.rehearsal,
        )

    def secrets(self) -> SecretStore:
        return SecretStore(
            self.layout,
            owner=self.owner,
            owner_gid=0 if self.owner == 0 else os.getegid(),
            proxy_gid=self.proxy_gid,
        )

    def config(self, env_file: Path | None = None) -> DeployConfig:
        path = env_file or self.layout.local(self.layout.deploy_env)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DeployConfigError([f"{path} cannot be read ({type(exc).__name__})"]) from exc
        return load_config(text, self.layout)


def _release_dir(value: Path | None) -> Path:
    directory = value or default_release_dir()
    if not (directory / "deploy" / "templates").is_dir():
        raise DeployError(f"{directory} is not an ATLAS release or checkout (no deploy/templates)")
    return directory


def _summary(config: DeployConfig) -> dict[str, Any]:
    return {
        "profile": config.profile,
        "resources": config.resources.name,
        "simulation_workers": config.resources.workers,
        "domain": config.domain,
        "api_bind": f"127.0.0.1:{config.api_port}",
        "allow_cidrs": list(config.allow_cidrs),
        "simulations_routed_by_nginx": config.profile == "private",
        "runtime_environment": runtime_environment(config),
        "api_key": "systemd credential (never shown)",
    }


def _validate(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    config = ctx.config(args.env_file)
    release = _release_dir(args.release_dir)
    renderer = Renderer.for_release(config, ctx.layout, release)
    files = renderer.files()
    problems: list[str] = []
    # The API's own settings parser must accept the rendered environment.
    from atlas.api.settings import load_api_settings
    from atlas.errors import ConfigError

    try:
        load_api_settings(repo_root=release, env=runtime_environment(config))
    except ConfigError as exc:
        problems.append(f"the API refuses the rendered environment: {exc}")
    if args.installed:
        ctx.ids()
        problems += [
            str(p)
            for p in ctx.secrets().check(
                proxy_key=config.profile == "private", proxy_users=config.profile == "private"
            )
        ]
        for path in (config.tls_cert, config.tls_key):
            if path is not None and not ctx.layout.local(path).exists():
                problems.append(f"{path} is missing (see 'HTTPS setup' in the guide)")
    payload = {
        "ok": not problems,
        "config": _summary(config),
        "files": [str(f.path) for f in files],
        "problems": problems,
    }
    text = "\n".join(
        [
            f"deploy.env: {'valid' if not problems else 'INVALID'} ({config.profile} profile, "
            f"{config.resources.name} resources, {config.resources.workers} worker(s))",
            *(f"  renders {f.path}" for f in files),
            *(f"  problem: {p}" for p in problems),
        ]
    )
    _emit(out, payload, args.json, text)
    return EXIT_OK if not problems else EXIT_FAILED


def _render(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    config = ctx.config()
    renderer = Renderer.for_release(config, ctx.layout, _release_dir(args.release_dir))
    files = renderer.files(args.stage)
    if args.apply:
        ctx.require_root()
        results = write_files(
            files,
            ctx.layout,
            chown=not ctx.rehearsal,
            group_ids={"root": 0, SERVICE_GROUP: ctx.service_gid, PROXY_GROUP: ctx.proxy_gid},
        )
        if config.exposed:
            enable_site(ctx.layout)
        changed = [str(r.path) for r in results if r.changed]
        _emit(
            out,
            {"changed": changed},
            args.json,
            "changed: "
            + (", ".join(changed) or "nothing")
            + "\nthen: systemctl daemon-reload; nginx -t && systemctl reload nginx (if exposed)",
        )
        return EXIT_OK
    if args.output_dir is not None:
        scratch = Layout(root=args.output_dir)
        write_files(files, scratch, chown=False)
        _emit(
            out,
            {"written": [str(f.path) for f in files]},
            args.json,
            f"written under {args.output_dir}",
        )
        return EXIT_OK
    for item in files:
        out.write(f"# ===== {item.path} (mode {item.mode:04o}, group {item.group}) =====\n")
        out.write(item.content)
    return EXIT_OK


def _secrets(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    command = args.secrets_command
    if command == "check":
        ctx.ids()
        config = ctx.config()
        problems = ctx.secrets().check(
            proxy_key=config.profile == "private", proxy_users=config.profile == "private"
        )
        _emit(
            out,
            {"ok": not problems, "problems": [str(p) for p in problems]},
            args.json,
            "secrets: ok" if not problems else "\n".join(f"problem: {p}" for p in problems),
        )
        return EXIT_OK if not problems else EXIT_FAILED
    ctx.require_root()
    store = ctx.secrets()
    if command == "init":
        actions = store.init()
    elif command == "rotate":
        actions = store.rotate()
    elif command == "add-user":
        password = None
        if args.password_stdin:
            password = (
                sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass()
            )
        generated = store.add_user(args.name, password)
        actions = [f"user {args.name} saved in {ctx.layout.htpasswd}; reload nginx to apply"]
        if generated is not None:
            # Shown once, for the operator's password manager; never stored in clear.
            actions.append(f"generated password (shown once): {generated}")
    else:
        removed = store.remove_user(args.name)
        actions = [f"user {args.name} {'removed' if removed else 'not found'}"]
    _emit(out, {"actions": actions}, args.json, "\n".join(actions))
    return EXIT_OK


def _manifest(args: argparse.Namespace, out: TextIO) -> int:
    directory: Path = args.release_dir
    if args.manifest_command == "write":
        manifest = build_manifest(
            directory,
            release_id=directory.name,
            commit=args.commit,
            built_at=datetime.now(UTC).isoformat(timespec="seconds"),
            uv_version=args.uv_version,
            checks=args.check,
        )
        path = write_manifest(directory, manifest)
        _emit(out, {"written": str(path)}, args.json, f"wrote {path}")
        return EXIT_OK
    if args.manifest_command == "show":
        _emit(out, load_manifest(directory), True)
        return EXIT_OK
    problems = check_release(directory)
    _emit(
        out,
        {"ok": not problems, "problems": problems},
        args.json,
        f"{directory.name}: matches its manifest" if not problems else "\n".join(problems),
    )
    return EXIT_OK if not problems else EXIT_FAILED


def _verify(ctx: Context, args: argparse.Namespace, out: TextIO) -> int:
    deployment = ctx.deployment()
    current = deployment.current()
    if current is None:
        raise DeployError("no current release")
    config = ctx.config()
    target: Target = deployment.target(current, config)
    target.smoke_simulation = not args.no_simulation
    result = verify(target, timeout=args.timeout)
    _emit(out, result.to_dict(), args.json, f"release {current}\n" + result.render())
    return EXIT_OK if result.ok else EXIT_FAILED


def _outcome(record: dict[str, Any], out: TextIO, as_json: bool) -> int:
    lines = [f"{record.get('action')}: {record.get('outcome')} ({record.get('release')})"]
    if record.get("restored"):
        lines.append(f"restored release: {record['restored']}")
    lines += [f"  {item}" for item in record.get("detail", [])]
    _emit(out, record, as_json, "\n".join(lines))
    return EXIT_OK if record.get("outcome") in ("activated", "nothing_to_recover") else EXIT_FAILED


def _monitor(args: argparse.Namespace, out: TextIO) -> int:
    # Without a state directory (a manual run) the counters are not compared between runs.
    directory = args.state_dir or (
        Path(os.environ["STATE_DIRECTORY"]) if os.environ.get("STATE_DIRECTORY") else None
    )
    state_path = None if directory is None else directory / "state.json"
    probes = monitoring.system_probes(f"http://127.0.0.1:{args.api_port}", cafile=args.tls_ca)
    previous = {} if state_path is None else monitoring.load_state(state_path)
    report, state = monitoring.run_checks(
        probes, previous, tls_domain=args.tls_domain, tls_port=args.tls_port
    )
    if state_path is not None:
        monitoring.save_state(state_path, state)
    out.write(json.dumps(report.to_dict(), sort_keys=True) + "\n")
    return EXIT_FAILED if report.status == monitoring.CRITICAL else EXIT_OK


def dispatch(args: argparse.Namespace, out: TextIO) -> int:
    ctx = Context(args.root)
    command = args.command
    if command == "preflight":
        argv = ["--resources", args.resources]
        argv += ["--release-dir", str(args.release_dir)] if args.release_dir else []
        argv += ["--probe-dependencies"] if args.probe_dependencies else []
        argv += ["--offline"] if args.offline else []
        argv += ["--json"] if args.json else []
        return preflight.main(argv)
    if command == "validate":
        return _validate(ctx, args, out)
    if command == "render":
        return _render(ctx, args, out)
    if command == "secrets":
        return _secrets(ctx, args, out)
    if command == "manifest":
        return _manifest(args, out)
    if command == "verify":
        return _verify(ctx, args, out)
    if command == "monitor":
        return _monitor(args, out)
    deployment = ctx.deployment()
    if command == "status":
        status = deployment.status()
        _emit(out, status, True)
        return EXIT_OK
    if command == "plan-upgrade":
        _emit(out, deployment.plan(args.release_id), True)
        return EXIT_OK
    ctx.require_root()
    deployment = ctx.deployment()
    if command == "activate":
        return _outcome(deployment.activate(args.release_id), out, args.json)
    if command == "rollback":
        return _outcome(deployment.rollback(args.to), out, args.json)
    if command == "recover":
        return _outcome(deployment.recover(), out, args.json)
    removed = deployment.prune(args.keep)
    _emit(out, {"removed": removed}, args.json, "removed: " + (", ".join(removed) or "nothing"))
    return EXIT_OK


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return dispatch(args, out)
    except DeployConfigError as exc:
        sys.stderr.write("atlas-deploy: invalid deployment configuration:\n")
        sys.stderr.write("".join(f"  - {problem}\n" for problem in exc.problems))
        return EXIT_USAGE
    except (DeployError, RenderError, RouteError, ManifestError, SecretError) as exc:
        sys.stderr.write(f"atlas-deploy: error: {exc}\n")
        return EXIT_USAGE
    except Exception:  # a bug, not an operational result
        sys.stderr.write("atlas-deploy: internal error (please report):\n" + traceback.format_exc())
        return EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
