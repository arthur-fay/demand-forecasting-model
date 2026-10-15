#!/usr/bin/env bash
# Build an immutable ATLAS release from an exact commit (M6). Run as root on the server:
#
#   sudo deploy/scripts/build-release.sh <40-character commit SHA>
#   sudo deploy/scripts/build-release.sh <sha> --repo https://github.com/arthur-fay/demand-forecasting-model.git
#   sudo deploy/scripts/build-release.sh <sha> --skip-reproduction     # faster, weaker checks
#
# Steps: fetch exactly that commit; export it (no .git) into
# /opt/atlas/releases/<version>-<sha12>; install the locked runtime dependencies with the
# hash-pinned uv (`uv sync --locked --no-dev`, hashes from uv.lock, Python 3.12, bytecode
# compiled); make the tree root-owned and read-only (group atlas may read); run the release's
# own checks as the unprivileged atlas user (snapshot verification and validation, the M4
# reference run reproduced byte for byte and inspected, the API contract equal to the
# committed one); write release.json last.
#
# Idempotent: a complete release of that commit is checked and kept. A failed or interrupted
# build leaves nothing behind (an incomplete directory is removed on the next run), and no
# other release, nor the running service, is touched. Activation is a separate step:
# atlas-deploy activate <release id>.
set -Eeuo pipefail
umask 022
export LC_ALL=C

INSTALL=/opt/atlas
RELEASES=$INSTALL/releases
UV=$INSTALL/tools/uv/bin/uv
CACHE=/var/cache/atlas/uv
PYTHON=/usr/bin/python3.12
STATE=/var/lib/atlas/deploy
REPO_URL=https://github.com/arthur-fay/demand-forecasting-model.git
REPRODUCE=1
COMMIT=

usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while (($#)); do
    case "$1" in
        --repo) REPO_URL=${2:?--repo needs a URL}; shift ;;
        --skip-reproduction) REPRODUCE=0 ;;
        -h | --help) usage; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) [[ -z $COMMIT ]] || { echo "one commit only" >&2; exit 2; }; COMMIT=$1 ;;
    esac
    shift
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'build-release: %s\n' "$*" >&2; exit 1; }

[[ $COMMIT =~ ^[0-9a-f]{40}$ ]] || die "give the full 40-character commit SHA (lowercase hex)"
((EUID == 0)) || die "run as root (sudo)"
[[ -x $UV ]] || die "$UV is missing: run deploy/scripts/bootstrap-host.sh --apply first"
[[ -x $PYTHON ]] || die "$PYTHON is missing (apt install python3.12 python3.12-venv)"
id -u atlas >/dev/null 2>&1 || die "the atlas user is missing: run the bootstrap script first"
[[ -d $RELEASES && -d $STATE ]] || die "the deployment layout is missing: run the bootstrap script"

# One build at a time.
exec 9>"$STATE/build.lock"
flock -n 9 || die "another release build is running"

STAGE=$(mktemp -d "$RELEASES/.build-XXXXXXXX")
RELEASE_DIR=
COMPLETE=0
cleanup() {
    local status=$?
    rm -rf -- "$STAGE"
    if ((COMPLETE == 0)) && [[ -n $RELEASE_DIR && -d $RELEASE_DIR ]]; then
        chmod -R u+w -- "$RELEASE_DIR" 2>/dev/null || true
        rm -rf -- "$RELEASE_DIR"
        echo "build-release: the incomplete release was removed; nothing else changed" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Tools run with a clean environment: no inherited PYTHONPATH, UV_* or proxy surprises. A
# proxy, if this network needs one, is passed through explicitly.
clean_env=(env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C)
for name in HTTPS_PROXY https_proxy NO_PROXY no_proxy SSL_CERT_FILE; do
    [[ -n ${!name:-} ]] && clean_env+=("$name=${!name}")
done

say "Fetching $COMMIT from $REPO_URL"
"${clean_env[@]}" HOME="$STAGE" git -C "$STAGE" init -q
"${clean_env[@]}" HOME="$STAGE" git -C "$STAGE" fetch -q --depth 1 --no-tags "$REPO_URL" "$COMMIT"
fetched=$(git -C "$STAGE" rev-parse FETCH_HEAD)
[[ $fetched == "$COMMIT" ]] || die "fetched $fetched instead of $COMMIT"

VERSION=$(git -C "$STAGE" show "$COMMIT:pyproject.toml" |
    "$PYTHON" -I -c 'import sys, tomllib; print(tomllib.load(sys.stdin.buffer)["project"]["version"])')
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unexpected project version: $VERSION"
RELEASE_ID="$VERSION-${COMMIT:0:12}"
target="$RELEASES/$RELEASE_ID"

if [[ -f $target/release.json ]]; then
    say "Release $RELEASE_ID exists: checking it against its manifest"
    "$target/.venv/bin/atlas-deploy" manifest check "$target" || die "$RELEASE_ID differs from its manifest; inspect it before deleting it"
    COMPLETE=1
    echo "$RELEASE_ID"
    exit 0
fi
if [[ -e $target ]]; then
    say "Removing the incomplete release $RELEASE_ID left by an earlier build"
    [[ $(readlink -f "$INSTALL/current" 2>/dev/null || true) != "$target" ]] || die "the incomplete release is current; roll back first"
    chmod -R u+w -- "$target"
    rm -rf -- "$target"
fi

RELEASE_DIR=$target
install -d -o root -g root -m 0700 "$RELEASE_DIR"
say "Exporting the commit into $RELEASE_DIR"
git -C "$STAGE" archive --format=tar "$COMMIT" | tar -x -C "$RELEASE_DIR" --no-same-owner --no-same-permissions

say "Installing the locked runtime dependencies (uv sync --locked --no-dev)"
uv_version=$("$UV" --version | awk '{print $2}')
(
    cd "$RELEASE_DIR"
    "${clean_env[@]}" HOME=/root UV_CACHE_DIR="$CACHE" UV_PYTHON_DOWNLOADS=never \
        UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_NO_PROGRESS=1 UV_NO_CONFIG=1 \
        "$UV" sync --locked --no-dev --python "$PYTHON" --project "$RELEASE_DIR"
)

say "Making the release root-owned and read-only (group atlas may read)"
chown -R root:atlas -- "$RELEASE_DIR"
chmod -R u=rX,g=rX,o= -- "$RELEASE_DIR"

as_atlas=(setpriv --reuid=atlas --regid=atlas --clear-groups --no-new-privs
    env -i PATH=/usr/bin:/bin LC_ALL=C HOME=/nonexistent PYTHONDONTWRITEBYTECODE=1
    ATLAS_REPO_ROOT="$RELEASE_DIR" OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1)
atlas=("${as_atlas[@]}" "$RELEASE_DIR/.venv/bin/atlas" --repo-root "$RELEASE_DIR")
checks=()

say "Check: the release reports version $VERSION"
reported=$("${atlas[@]}" --version | awk '{print $2}')
[[ $reported == "$VERSION" ]] || die "the release reports $reported, not $VERSION"
checks+=("version")

say "Check: snapshot integrity, validation and reproduction (atlas check)"
(cd /tmp && "${atlas[@]}" check >/dev/null) || die "atlas check failed: the committed snapshot is not intact"
checks+=("atlas check")

say "Check: the committed M4 reference run is intact (atlas optimize inspect)"
(cd /tmp && "${atlas[@]}" optimize inspect "$RELEASE_DIR/docs/results/atlas-m4-optimization-v1" >/dev/null) ||
    die "atlas optimize inspect failed"
checks+=("atlas optimize inspect")

if ((REPRODUCE)); then
    say "Check: the M4 reference run reproduces byte for byte here (about a minute)"
    (cd /tmp && "${atlas[@]}" optimize verify "$RELEASE_DIR/docs/results/atlas-m4-optimization-v1" >/dev/null) ||
        die "atlas optimize verify failed: this machine does not reproduce the committed results"
    checks+=("atlas optimize verify")
fi

say "Check: the API contract equals the committed docs/api/openapi.json"
(cd /tmp && "${atlas[@]}" api openapi) | cmp -s - "$RELEASE_DIR/docs/api/openapi.json" ||
    die "the API contract differs from docs/api/openapi.json"
checks+=("api contract")

say "Writing release.json"
manifest_args=()
for check in "${checks[@]}"; do manifest_args+=(--check "$check"); done
"$RELEASE_DIR/.venv/bin/atlas-deploy" manifest write "$RELEASE_DIR" \
    --commit "$COMMIT" --uv-version "$uv_version" "${manifest_args[@]}" >/dev/null
chown root:atlas -- "$RELEASE_DIR/release.json"
chmod 0440 -- "$RELEASE_DIR/release.json"
"$RELEASE_DIR/.venv/bin/atlas-deploy" manifest check "$RELEASE_DIR" >/dev/null ||
    die "the new release does not match its own manifest"

COMPLETE=1
say "Built $RELEASE_ID (checks: ${checks[*]})"
say "Next: atlas-deploy plan-upgrade $RELEASE_ID, then atlas-deploy activate $RELEASE_ID"
echo "$RELEASE_ID"
