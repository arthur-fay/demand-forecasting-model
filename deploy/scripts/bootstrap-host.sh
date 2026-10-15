#!/usr/bin/env bash
# Prepare an Ubuntu 24.04 LTS server for ATLAS (M6). Idempotent, and a dry run by default:
#
#   sudo deploy/scripts/bootstrap-host.sh --resources standard           # show the plan
#   sudo deploy/scripts/bootstrap-host.sh --resources standard --apply   # carry it out
#
# Run it from a checkout of the repository. It runs the read-only preflight first and stops
# on an UNSUITABLE server. Then it installs the Ubuntu packages ATLAS needs, creates the
# unprivileged `atlas` and `atlas-monitor` system users, the directories of the deployment layout with their
# owners and modes (src/atlas/deploy/layout.py), a hash-pinned uv for release builds, and
# /etc/atlas/deploy.env from the example (never overwritten). It disables nginx's stock
# default site, which would conflict with the ATLAS site.
#
# It never changes the firewall, SSH, DNS or certificates, and creates no secret: those are
# explicit operator steps of docs/DEPLOYMENT.md.
set -Eeuo pipefail
umask 022
export LC_ALL=C

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
APPLY=0
RESOURCES=standard
WITH_CERTBOT=0
PYTHON=/usr/bin/python3.12

usage() {
    sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    echo "Options: --apply  --resources low|standard  --with-certbot"
}

while (($#)); do
    case "$1" in
        --apply) APPLY=1 ;;
        --resources) RESOURCES=${2:?--resources needs a value}; shift ;;
        --with-certbot) WITH_CERTBOT=1 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done
[[ $RESOURCES == low || $RESOURCES == standard ]] || { echo "--resources: low or standard" >&2; exit 2; }

say() { printf '==> %s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }
# Run a command, or (dry run) print it.
run() {
    if ((APPLY)); then
        "$@"
    else
        printf '    would run:'
        printf ' %q' "$@"
        printf '\n'
    fi
}

((EUID == 0)) || die "run as root (sudo); without --apply nothing is changed"
# shellcheck source=/dev/null
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || die "Ubuntu LTS is required (found ${PRETTY_NAME:-unknown})"

say "Preflight (read-only)"
preflight_python=$(command -v python3.12 || command -v python3 || true)
[[ -n $preflight_python ]] || die "no python3 to run the preflight (apt install python3)"
if ! "$preflight_python" -I "$REPO/src/atlas/deploy/preflight.py" \
    --resources "$RESOURCES" --release-dir "$REPO" --offline --before-bootstrap; then
    die "the preflight found this server UNSUITABLE: resolve every [FAIL] finding first"
fi

say "Ubuntu packages"
packages=(nginx python3.12 python3.12-venv git openssl ca-certificates)
((WITH_CERTBOT)) && packages+=(certbot)
missing=()
for package in "${packages[@]}"; do
    dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed" ||
        missing+=("$package")
done
if ((${#missing[@]})); then
    run apt-get update -qq
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}"
else
    echo "    already installed: ${packages[*]}"
fi
if [[ -L /etc/nginx/sites-enabled/default ]]; then
    say "Disable nginx's stock default site (it claims default_server on port 80)"
    run rm /etc/nginx/sites-enabled/default
fi

say "Service account"
if getent group atlas >/dev/null; then
    echo "    group atlas exists"
else
    run groupadd --system atlas
fi
if id -u atlas >/dev/null 2>&1; then
    echo "    user atlas exists"
else
    run useradd --system --gid atlas --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin --comment "ATLAS API service" atlas
fi
# The monitor's own account (its unit adds the groups atlas and systemd-journal).
if id -u atlas-monitor >/dev/null 2>&1; then
    echo "    user atlas-monitor exists"
else
    run useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin --comment "ATLAS monitor" atlas-monitor
fi

say "Directories (owner, group, mode)"
# path owner group mode
layout=(
    "/opt/atlas root root 0755"
    "/opt/atlas/releases root atlas 0750"
    "/opt/atlas/tools root root 0755"
    "/etc/atlas root root 0755"
    "/etc/atlas/credentials root root 0700"
    "/etc/nginx/atlas root www-data 0750"
    "/var/lib/atlas root root 0755"
    "/var/lib/atlas/deploy root root 0700"
    "/var/cache/atlas root root 0700"
    "/var/cache/atlas/uv root root 0700"
    "/var/www/atlas-acme root root 0755"
)
for entry in "${layout[@]}"; do
    read -r path owner group mode <<<"$entry"
    run install -d -o "$owner" -g "$group" -m "$mode" "$path"
done

say "Hash-pinned uv for release builds"
uv_pin=$(sed -n 's/^uv==\([0-9.]*\).*/\1/p' "$REPO/.github/ci-requirements.txt")
uv_now=$(/opt/atlas/tools/uv/bin/uv --version 2>/dev/null | awk '{print $2}' || true)
if [[ $uv_now == "$uv_pin" ]]; then
    echo "    uv $uv_now already installed"
else
    run "$PYTHON" -I -m venv --clear /opt/atlas/tools/uv
    run /opt/atlas/tools/uv/bin/python -I -m pip install --quiet --disable-pip-version-check \
        --no-cache-dir --require-hashes -r "$REPO/.github/ci-requirements.txt"
fi

say "Deployment configuration"
if [[ -e /etc/atlas/deploy.env ]]; then
    echo "    /etc/atlas/deploy.env exists (kept as it is)"
else
    run install -o root -g root -m 0644 "$REPO/deploy/deploy.env.example" /etc/atlas/deploy.env
    if ((APPLY)); then
        sed -i "s/^ATLAS_DEPLOY_RESOURCES=.*/ATLAS_DEPLOY_RESOURCES=$RESOURCES/" /etc/atlas/deploy.env
    fi
fi

if ((APPLY)); then
    say "Done. Next: sudo $REPO/deploy/scripts/build-release.sh <commit> (docs/DEPLOYMENT.md, section 5)"
else
    say "Dry run: nothing was changed. Re-run with --apply to carry out the plan above."
fi
