"""After a publication (M9): the site serves the committed static build, byte for byte.

The publication job of .github/workflows/pages.yml runs this once GitHub Pages reports the
deployment. Every file the committed manifest (web/atlas-static-site.json) lists, and the
manifest itself, is fetched from the published site and compared with its recorded size and
SHA-256. A publication takes a little while to reach every edge, so the whole check is
repeated a few times before it fails. It names every file that differs.

Standard library only. Usage::

    python3 .github/scripts/pages_check_site.py --manifest web/atlas-static-site.json \\
        --url "$PAGE_URL" [--attempts 10] [--wait 15]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

USER_AGENT = "atlas-pages-check/1"


def fetch(url: str, timeout: float) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def differences(manifest: dict[str, Any], committed: bytes, base: str, timeout: float) -> list[str]:
    """The files the site does not serve as recorded (empty: it serves the build)."""
    found: list[str] = []
    served = fetch(base + "atlas-static-site.json", timeout)
    if served != committed:
        found.append("atlas-static-site.json")
    for path, entry in sorted(manifest["files"].items()):
        body = fetch(base + quote(path), timeout)
        if (
            body is None
            or len(body) != entry["bytes"]
            or hashlib.sha256(body).hexdigest() != entry["sha256"]
        ):
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--url", required=True, help="The site's address (the base path).")
    parser.add_argument("--attempts", type=int, default=10)
    parser.add_argument("--wait", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--allow-http", action="store_true", help="Accept http:// (tests on this machine only)."
    )
    args = parser.parse_args(argv)
    scheme = urlsplit(args.url).scheme
    if scheme != "https" and not (args.allow_http and scheme == "http"):
        print(f"pages check: the site's address must be https, not {args.url}", file=sys.stderr)
        return 1
    committed = args.manifest.read_bytes()
    manifest = json.loads(committed)
    base = args.url if args.url.endswith("/") else args.url + "/"
    found: list[str] = []
    for attempt in range(1, max(1, args.attempts) + 1):
        found = differences(manifest, committed, base, args.timeout)
        if not found:
            print(
                f"pages check: the site serves the committed build ({len(manifest['files'])} files)"
            )
            return 0
        if attempt < args.attempts:
            time.sleep(args.wait)
    print(f"pages check: {len(found)} file(s) differ from the committed build:", file=sys.stderr)
    for path in found[:50]:
        print(f"  - {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
