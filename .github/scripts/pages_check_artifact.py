"""Before a publication (M9): the artifact to publish is the committed static build, exactly.

The publication job of .github/workflows/pages.yml runs this on its own runner, which has
run no npm code, after downloading the ``github-pages`` artifact the build job uploaded and
before deploy-pages publishes it. The archive (``artifact.tar``, written by
actions/upload-pages-artifact) must hold exactly the files the committed manifest
(web/atlas-static-site.json) lists, each with its recorded size and SHA-256, and that
manifest itself, byte for byte: no other file, no link or special file, no path that leaves
the archive, and nothing after the archive's end (tarfile stops reading at a damaged header
or an early end marker, where another reader might go on). It names every difference.

Standard library only. Usage::

    python3 .github/scripts/pages_check_artifact.py --manifest web/atlas-static-site.json \\
        --artifact "$RUNNER_TEMP/pages-artifact/artifact.tar"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath

NAME = "atlas-static-site.json"


def _name(member: tarfile.TarInfo) -> str | None:
    """The member's path in the site ('' for the root), or None when it is no such path."""
    name = member.name.removeprefix("./").rstrip("/")
    if name in ("", "."):
        return ""
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or any(part in ("", ".", "..") for part in path.parts):
        return None
    return name


def problems(committed: bytes, archive: Path) -> list[str]:
    """How the archive differs from the committed build (empty: it is the build)."""
    files = json.loads(committed)["files"]
    expected = {path: (entry["bytes"], entry["sha256"]) for path, entry in files.items()}
    directories = {
        str(parent)
        for path in [*expected, NAME]
        for parent in PurePosixPath(path).parents
        if str(parent) != "."
    }
    found: list[str] = []
    contents: dict[str, bytes] = {}
    try:
        with tarfile.open(archive, mode="r:") as tar:
            for member in tar:
                name = _name(member)
                if name is None:
                    found.append(f"a path outside the site: {member.name}")
                elif member.isdir():
                    if name != "" and name not in directories:
                        found.append(f"a directory the build does not have: {name}")
                elif not member.isfile():
                    found.append(f"not a regular file: {name}")
                elif name in contents:
                    found.append(f"twice in the archive: {name}")
                elif name != NAME and name not in expected:
                    found.append(f"not in the committed build: {name}")
                else:
                    stream = tar.extractfile(member)
                    contents[name] = stream.read() if stream is not None else b""
            end = tar.offset
        with archive.open("rb") as raw:
            raw.seek(end)
            rest = raw.read()
    except (OSError, tarfile.TarError) as exc:
        return [f"the artifact cannot be read ({exc})"]
    # A well-formed archive ends with two zero blocks (and zero padding): anything else after
    # the last member read is data a more lenient reader could extract.
    if len(rest) < 2 * tarfile.BLOCKSIZE or rest.strip(b"\0"):
        found.append("data after the last member that is not the end of the archive")
    if NAME not in contents:
        found.append(f"missing: {NAME}")
    elif contents[NAME] != committed:
        found.append(f"{NAME} differs from the committed web/{NAME}")
    for path, (size, sha256) in sorted(expected.items()):
        body = contents.get(path)
        if body is None:
            found.append(f"missing: {path}")
        elif len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
            found.append(f"altered: {path}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    committed = args.manifest.read_bytes()
    found = problems(committed, args.artifact)
    if found:
        print(
            f"artifact check: {len(found)} difference(s) from the committed build:", file=sys.stderr
        )
        for problem in found[:50]:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    count = len(json.loads(committed)["files"])
    print(f"artifact check: the artifact is the committed build ({count} files and {NAME})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
