"""Compare a regenerated run with a committed one, file by file, through their manifests.

Usage: python .github/scripts/same_outputs.py <committed run dir> <regenerated run dir>

For the runs that have no ``verify`` command (the M2 and M3 projections and the legacy
one-step holdout). Both manifests must record the same output files with the same SHA-256,
and every output kept in the committed directory must still hash to its recorded value.
Exit status 0 when everything is identical, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def _outputs(run_dir: Path) -> tuple[str, dict[str, str]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    return str(manifest.get("record_type")), dict(manifest.get("outputs", {}))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    committed_dir, regenerated_dir = Path(argv[1]), Path(argv[2])
    committed_type, committed = _outputs(committed_dir)
    regenerated_type, regenerated = _outputs(regenerated_dir)
    problems: list[str] = []
    if committed_type != regenerated_type:
        problems.append(f"record type {regenerated_type!r}, committed {committed_type!r}")
    if not committed:
        problems.append("the committed manifest records no outputs")
    for name in sorted(set(committed) | set(regenerated)):
        same = committed.get(name) == regenerated.get(name)
        print(f"{name:40} {'identical' if same else 'DIFFERENT'}")
        if not same:
            problems.append(
                f"{name}: SHA-256 {regenerated.get(name)}, committed {committed.get(name)}"
            )
        kept = committed_dir / name
        if (
            name in committed
            and kept.is_file()
            and hashlib.sha256(kept.read_bytes()).hexdigest() != committed[name]
        ):
            problems.append(f"{kept} no longer matches its manifest")
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{len(committed)} recorded output(s); {'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
