# Deployment and operations (M6)

This guide explains how ATLAS runs on one small Ubuntu server, how to install it, operate
it, upgrade it, roll it back and recover it. It is written for a technically capable person
who did not build ATLAS. Everything it describes is in the repository: the tooling in
`src/atlas/deploy/` (the `atlas-deploy` command), the host scripts in `deploy/scripts/`, the
templates in `deploy/templates/`.

> **Status: M6 phase A.** This is a tested deployment *package*. Nothing has been deployed
> to any server, domain or DNS record. Deploying it (phase B) needs separate approval; the
> exact requirements are in [section 18](#18-authorising-phase-b).
>
> **M7** adds the web dashboard to every release: nginx serves a verified copy of it next to
> the API, and activation, verification and rollback cover it with the API
> ([section 19](#19-the-dashboard-m7), [DASHBOARD.md](DASHBOARD.md)).
>
> **What the deployed software is.** ATLAS serves research outputs. The forecast is an
> unvalidated point forecast; the M3 prediction intervals have the status
> **FAILED_CALIBRATION**; every M4 operational parameter is **HYPOTHETICAL**. The API says so
> on every response (`integrity` block, `validated_recommendation: false` fixed by the
> schema), and the deployment checks that it still does after every activation
> ([section 8](#8-starting-stopping-and-checking-health)). **A deployed ATLAS is not a validated NYC staffing or
> resource-allocation system** and must never be presented as one.

Contents:

1. Architecture
2. Deployment method and why
3. Server requirements and resource estimates
4. Preflight assessment
5. Ubuntu preparation and secure installation
6. Configuration
7. HTTPS setup
8. Starting, stopping and checking health
9. Troubleshooting
10. Upgrading
11. Rolling back and recovering
12. Backups and recovery
13. Security considerations
14. Monitoring and alerting
15. Reliability design
16. Testing
17. Remaining limitations
18. Authorising phase B
19. The dashboard (M7)
20. Independent review

## 1. Architecture

```
  desktop / mobile browser                       operator laptop
        │ HTTPS :443 (TLS 1.2/1.3, HSTS)               │ SSH :22 (keys only)
        │ HTTP :80 (ACME challenges, redirect)         │
┌───────┼──────────────── Ubuntu 24.04 LTS server ─────┼───────────────────────────┐
│ ufw: deny incoming, except 22, 80, 443 (tunnel profile: 22 only)                  │
│       ▼                                              ▼                            │
│  nginx (www-data)                                 sudo atlas-deploy ...            │
│   - HTTP Basic accounts (private profile)            activate / rollback / verify │
│   - routes = operations of docs/api/openapi.json, each with its methods; else 404 │
│   - per-client limits: 10 req/s (burst 40), simulations 30/min (burst 6; low     │
│     profile 12/min, burst 3), 16 connections                                      │
│   - 16 KiB bodies, 10 s header/body deadlines, ambiguous paths refused (400)       │
│   - passes no client header but Content-Type and Accept; adds X-API-Key to        │
│     POST /api/v1/simulations only                                                  │
│       │                                                                            │
│       ▼ unix:/run/atlas/api.sock (the service's own directory: no other account   │
│       │  can create it); the API also listens on 127.0.0.1:8000 for SSH tunnels,   │
│       │  verification and the monitor (systemd denies it any other address)       │
│  atlas.service: user atlas, sandboxed, Type=notify, watchdog 30 s                 │
│   uvicorn + FastAPI (the M5 API) ──pipes──► 1 or 2 spawned simulation workers     │
│   WorkingDirectory=/opt/atlas/current ─► releases/<version>-<commit12> (read-only)│
│   simulation key: systemd credential (LoadCredential), never in the environment  │
│                                                                                    │
│  atlas-monitor.timer (every minute) ─► atlas-monitor.service (user atlas-monitor) │
│        └─ a critical finding ─► atlas-alert@.service (optional operator hook)      │
│  journald (persistent, capped)    certbot.timer (renewal ─► nginx reload)          │
└────────────────────────────────────────────────────────────────────────────────────┘
```

In the private and public-demo profiles nginx also serves the dashboard (M7): its pages and
files by their exact names from a verified copy of the release's build, with the
dashboard's own Content-Security-Policy, behind the same accounts and allow-list, with its
own per-client limit (20 requests/s, burst 60) so that loading the page never uses up the
API's; only its deployment descriptor, one small request per page load, counts against the
API's read limit ([section 19](#19-the-dashboard-m7)).

On disk (`src/atlas/deploy/layout.py`):

| Path | Owner, mode | Content |
|---|---|---|
| `/opt/atlas/releases/<id>/` | root:atlas, files 0440/0550 | one immutable release: the exported commit (no `.git`), its `.venv`, `release.json` |
| `/opt/atlas/current` | link | `releases/<id>`, replaced atomically on activation |
| `/opt/atlas/dashboard/<id>/` | root:www-data, dirs 0750, files 0640 | (M7) the release's dashboard build as nginx serves it: a verified copy made at activation, one per release |
| `/opt/atlas/tools/uv/` | root, 0755 | the hash-pinned uv (0.11.32) used by release builds |
| `/etc/atlas/deploy.env` | root, 0644 | the deployment configuration ([section 6](#6-configuration)); no secret |
| `/etc/atlas/atlas.env` | root, 0644 | the API's environment, rendered from `deploy.env`; no secret |
| `/etc/atlas/credentials/api-key` | root, 0600 (dir 0700) | the simulation key |
| `/etc/nginx/atlas/htpasswd` | root:www-data, 0640 | proxy accounts (SHA-512-crypt) |
| `/etc/nginx/atlas/simulation-key.conf` | root, 0600 | the key nginx adds to simulations |
| `/etc/nginx/sites-available/atlas.conf` | root, 0644 | the rendered site (linked into `sites-enabled`) |
| `/etc/systemd/system/atlas*.service`, `.timer` | root, 0644 | the rendered units |
| `/etc/systemd/journald.conf.d/60-atlas.conf` | root, 0644 | persistent journal, 200-500 MB cap, 30 days |
| `/var/lib/atlas/deploy/` | root, 0700 | `history.jsonl` (every deployment step), the deployment and build locks, `last-good.json` (the last verified release and the `deploy.env` it was verified with) |
| `/run/atlas/` | atlas, 0755 | `api.sock`, the API's socket for nginx (created by systemd, `RuntimeDirectory`) |
| `/var/lib/atlas-monitor/` | atlas-monitor, 0700 | the monitor's counters between runs (`StateDirectory`) |
| `/var/cache/atlas/uv/` | root, 0700 | download cache of release builds |
| `/var/www/atlas-acme/` | root, 0755 | ACME HTTP-01 challenge files |

A **release id** is `<version>-<first 12 hex digits of the commit>`, e.g. `1.0.0-0123456789ab`
for a commit `0123456789ab...` (the examples below write `<release id>`).

## 2. Deployment method and why

**Native systemd services, nginx from Ubuntu, no containers.** Evaluated for a small VPS:

| | Native systemd (chosen) | Docker / Compose | Podman (rootless) |
|---|---|---|---|
| Extra memory | none | dockerd + containerd: about 140 MiB resident (measured on the test machine: 95 + 48 MiB) | none (no daemon) |
| Isolation | systemd sandbox: no capabilities, read-only system, private /tmp and devices, user namespace, syscall filter, loopback-only IP firewall; `systemd-analyze security` exposure **0.9 (SAFE)** | namespaces + seccomp; default profile weaker than the unit's | similar to Docker |
| Firewall | ufw rules apply | published ports bypass ufw (Docker's own iptables chains): a classic accidental exposure | rules apply |
| Security updates | unattended-upgrades patches nginx, OpenSSL, Python; a release's own `.venv` changes only by building and activating a new release (CI's weekly dependency audit reports advisories; see DEVELOPMENT.md, "Dependency maintenance") | images must be rebuilt | images must be rebuilt |
| Reproducibility | exact commit + `uv.lock` hashes + Python 3.12 from Ubuntu; the release reproduces the M4 reference run byte for byte *on the server* before it can be activated | also pins OS libraries (stronger) | same as Docker |
| Rollback | atomic link to the previous release | previous image tag | previous image tag |
| Moving parts | systemd, nginx | + daemon, images, registry | + images, registry |

The decisive points on a 1-2 GB server are the memory a container daemon costs, the ufw
bypass, and that systemd already provides comparable isolation (measured) with automatic
restarts, a watchdog, resource limits, credentials and journald. The price is that OS
libraries are not pinned; it is mitigated by the build-time reproduction check and the
post-deployment verification. Only one implementation is maintained.

**nginx rather than Caddy** (also considered): nginx is in Ubuntu *main* (security
maintained by Canonical), small (about 20 MiB with 2 workers), and has per-client request
and connection limits, request buffering and read deadlines built in. Caddy's automatic
HTTPS is attractive, but Ubuntu ships it in *universe* and per-client rate limiting needs a
third-party plugin. Certificates come from certbot (Ubuntu package, systemd timer).

Not used, as the brief requires: Kubernetes, orchestration, message brokers, managed cloud
services, a database.

## 3. Server requirements and resource estimates

| | Minimum (`low` profile) | Recommended (`standard` profile) |
|---|---|---|
| OS | Ubuntu 24.04 LTS (tested); 22.04 and 26.04 need a uv-managed Python 3.12 (untested) | Ubuntu 24.04 LTS |
| CPU | 1 vCPU, x86-64-v2 (SSE4.2, POPCNT) or aarch64 | 2 vCPU |
| Memory | 1 GB **and** 1 GB swap | 2 GB (swap still advised) |
| Disk | 10 GB (at least 4 GB free) | 20 GB |
| Simulation workers | 1 (one simulation at a time) | 2 |
| Limits | MemoryHigh 400M, MemoryMax 512M, TasksMax 96; simulations 12/min per client | MemoryHigh 640M, MemoryMax 768M, TasksMax 128; 30/min |
| Network | public IPv4 (IPv6 optional), outbound HTTPS to GitHub, PyPI, Let's Encrypt | same |

Generic virtual CPUs (`qemu64`) without SSE4.2 cannot import the locked NumPy: the
preflight refuses them. armv7, i386 and musl-based systems have no locked wheels.

**Memory**, measured under systemd in the disposable test server (section 16), with the
release environment and the sandboxed unit:

| Component | Measured | Budgeted (preflight) |
|---|---|---|
| API server process | 180-184 MiB (43 threads under load) | 190 MiB |
| each simulation worker | 120-126 MiB | 135 MiB |
| multiprocessing resource tracker | 12 MiB | 15 MiB |
| whole `atlas.service` cgroup, 2 workers, after load | 282-305 MiB | 475 MiB |
| nginx | 17-18 MiB (master and workers, after the test load) | 20 MiB |
| monitor run (once a minute, a few seconds) | about 25 MiB | 30 MiB |
| operating system | 250-350 MiB on a typical Ubuntu server (the preflight measures it) | measured |
| release build (while the old release serves) | uv, then `atlas check` and the M4 reproduction | +400 MiB |

So the service needs about 0.33 GiB with one worker and 0.45 GiB with two; with nginx, the
monitor and Ubuntu, 1 GB is enough for the `low` profile only with swap, and 2 GB gives the
`standard` profile ample headroom. The limits in the unit leave room above the measured use;
the workers raise their own OOM score, so under memory pressure the kernel kills a worker
(replaced by the pool, one simulation lost) before the server.

**Disk**: one release is about 320 MB (the export 17 MB, the environment about 300 MB).
Nothing deletes releases automatically: `atlas-deploy prune` keeps three (run it after a
successful upgrade; the monitor warns below 15 % or 1 GiB free). The uv cache is about
220 MB (each build drops what nothing references any more), the journal at most 500 MB,
nginx logs 14 days.

**CPU and time**: GET endpoints answer in single-digit milliseconds; a simulation takes 0.2-0.4 s
(at worst about 1.5 s) of one CPU (docs/API.md section 9). A release build took 49-56 s on the
4-core test machine (the M4 reproduction is most of it); expect 2-4 minutes on 1 vCPU.
Startup to ready: 3-6 s.

## 4. Preflight assessment

Run it on the candidate server **before anything else**. It reads, never changes, and needs
only the system Python:

```bash
git clone https://github.com/arthur-fay/demand-forecasting-model.git ~/atlas   # any user
cd ~/atlas
python3 -I src/atlas/deploy/preflight.py --resources standard --release-dir .
sudo python3 -I src/atlas/deploy/preflight.py --resources low --release-dir .   # root sees more
python3 -I src/atlas/deploy/preflight.py --resources standard --release-dir . --json > preflight.json
```

It checks the distribution and version, kernel and systemd; the CPU architecture, the
x86-64-v2 instruction set and the CPUs available; total and available memory, swap and
memory pressure; free disk where releases, caches, logs and temporary files go; load,
pressure, the largest processes and competing services (Docker, Apache, databases);
listening sockets (port 8000 must be free, 80/443 free or nginx, other exposed listeners);
Python 3.12 with venv, git, openssl, nginx, uv; the firewall, SSH authentication, automatic
security updates and clock synchronisation (as root); outbound HTTPS; and whether every
locked runtime package has a wheel for this platform (from `uv.lock`).

`--probe-dependencies` also installs the locked runtime dependencies into a temporary
directory (with the hash-pinned uv), imports them and solves a small LP with HiGHS, then
deletes the directory: the definitive "can the locked ATLAS dependencies execute here". On
the test server it took 15-16 s (it downloads about 120 MB), with a 3-4 s import
and 133-136 MiB peak memory. It needs `python3.12-venv` (run it after the bootstrap, or install
that package first). Run the probe as an ordinary user: it needs no privilege, and as root
it would import the downloaded packages as root.

Each finding is `pass`, `info`, `warn` or `fail`, with the requirement and the action to
take. Any `fail` makes the verdict **UNSUITABLE** and the exit status 1: do not use the
server until it is resolved. It also prints the memory estimate for the chosen profile and
recommends a profile. Example (abbreviated):

```
ATLAS preflight (profile: standard): UNSUITABLE
[FAIL] cpu: instruction set    missing: sse4_1, sse4_2, popcnt
                               action:   The locked NumPy refuses to import on this CPU. Many
                                         providers expose a generic virtual CPU ('qemu64'); ...
[WARN] memory: swap            no swap
                               action:   Add a swap file ... fallocate -l 1G /swapfile ...
[PASS] os: distribution        Ubuntu 24.04.3 LTS
...
Memory estimate (MiB) with 2 simulation worker(s): ... ATLAS total (with nginx, monitor) 525
Recommended profile for this server: low
```

## 5. Ubuntu preparation and secure installation

All commands as an administrator with `sudo`. Phase B only.

**5.1 Before ATLAS** (operator decisions, never automated):

```bash
# Key-only SSH (keep a working session open while testing):
sudo tee /etc/ssh/sshd_config.d/60-atlas.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && sudo systemctl reload ssh

sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades
sudo timedatectl set-ntp true
# 1 GB servers: a swap file
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile \
  && sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Firewall (tunnel profile: OpenSSH only; private profile: also 80 and 443):

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit OpenSSH          # also rate-limits repeated SSH connections
sudo ufw allow 80/tcp           # private profile: ACME challenges and the HTTPS redirect
sudo ufw allow 443/tcp          # private profile
sudo ufw enable && sudo ufw status verbose
```

Optionally restrict SSH to known networks (`sudo ufw allow from 203.0.113.0/24 to any port 22`)
and remove the general rule. If you set `ATLAS_DEPLOY_ALLOW_CIDRS`, enforce the same networks
in ufw as well (`sudo ufw allow from 203.0.113.0/24 to any port 443` instead of
`allow 443/tcp`): nginx refuses other networks with a 403, but only after their TLS
handshake, and answers a wrong method (405) or an ambiguous path (400) before its
allow-list.

**5.2 Bootstrap** (idempotent; a dry run unless `--apply`):

```bash
cd ~/atlas
sudo deploy/scripts/bootstrap-host.sh --resources standard            # read the plan
sudo deploy/scripts/bootstrap-host.sh --resources standard --apply    # do it
```

It runs the preflight (and stops on UNSUITABLE), installs `nginx python3.12 python3.12-venv
git openssl ca-certificates` (`--with-certbot` adds certbot), disables nginx's stock default
site, creates the system users `atlas` (the service) and `atlas-monitor` (the monitor; no
login shell, no home for either), the directories of section 1
with their owners and modes, a hash-pinned uv in `/opt/atlas/tools/uv` (pip
`--require-hashes`, the same pins as CI) and `/etc/atlas/deploy.env` from
`deploy/deploy.env.example` (tunnel profile; an existing file is never overwritten). It
never touches the firewall, SSH, DNS or certificates and creates no secret.

**5.3 Build a release** from an exact, reviewed commit (merged to `main`, CI green):

```bash
sudo deploy/scripts/build-release.sh <40-character commit SHA>   # prints the release id
```

It fetches exactly that commit (`git fetch --depth 1 <url> <sha>`, then checks the SHA),
exports it without `.git` into `/opt/atlas/releases/<id>`, installs the locked runtime
dependencies (`uv sync --locked --no-dev`: every wheel checked against the hashes in
`uv.lock`; no development tools; no Python download), makes the tree root-owned and
read-only (group `atlas` may read; others nothing), then runs the release's own checks **as
the unprivileged `atlas` user** (`setpriv --no-new-privs`): `atlas check` (snapshot integrity,
validation, reproduction), `atlas optimize inspect` and `atlas optimize verify` (the committed
M4 reference run reproduced byte for byte on this machine; `--skip-reproduction` omits it),
and the API contract equal to `docs/api/openapi.json`. The project's own code is compiled
to bytecode during the build, so no later run writes into the release. `--repo` takes an
`https://` or `file:///` URL or an absolute path only; git refuses a local repository that
root does not own ("dubious ownership"), and the build does not override that. Only then is `release.json` written
([section 10](#10-upgrading)). A failed or interrupted build removes its directory; an
existing complete release is checked and kept.

**5.4 Secrets and first activation** (tunnel profile):

```bash
ID=<release id>                      # printed by the build, e.g. 1.0.0-0123456789ab
R=/opt/atlas/releases/$ID/.venv/bin/atlas-deploy
sudo $R secrets init                 # the simulation key, root-only, never printed
sudo $R validate --installed         # deploy.env, rendered files, secrets, permissions
sudo $R activate $ID                 # render, install, switch, restart, verify
```

From now on use `/opt/atlas/current/.venv/bin/atlas-deploy` (below simply `atlas-deploy`;
`sudo ln -s /opt/atlas/current/.venv/bin/atlas-deploy /usr/local/sbin/` makes it so).

Reach the tunnel deployment from your machine: `ssh -L 8000:127.0.0.1:8000 admin@server`, then
`http://localhost:8000/health/ready` and the API in a browser or `curl`. Simulations need the
key (`sudo cat /etc/atlas/credentials/api-key`, header `X-API-Key`). Send it only while
`systemctl status atlas` shows the service running: while it is down, another local
account could listen on port 8000 (`atlas-deploy verify` checks this before it sends the
key; nginx never uses that port).

## 6. Configuration

`/etc/atlas/deploy.env` is the only file you edit. Everything else is rendered from it by the
release itself, so the unit, the API environment and the nginx site cannot disagree. One
`KEY=value` per line; quotes, spaces, shell syntax, unknown and repeated keys are refused.

| Key | Default | Meaning |
|---|---|---|
| `ATLAS_DEPLOY_PROFILE` | `tunnel` | `tunnel`, `private` or `public-demo` (below) |
| `ATLAS_DEPLOY_RESOURCES` | `standard` | `low` (1 worker) or `standard` (2) |
| `ATLAS_DEPLOY_DOMAIN` | none | the certificate name; required by `private` and `public-demo` |
| `ATLAS_DEPLOY_TLS_CERT`, `_TLS_KEY` | `/etc/letsencrypt/live/<domain>/fullchain.pem`, `privkey.pem` | certificate files |
| `ATLAS_DEPLOY_ALLOW_CIDRS` | none | networks allowed to connect to nginx (e.g. a VPN); `/0` is refused |
| `ATLAS_DEPLOY_HSTS_MAX_AGE` | 31536000 | 300-63072000 seconds |
| `ATLAS_DEPLOY_IPV6` | `false` | whether nginx listens on IPv6; off by default because the per-client limits count each address and one IPv6 client usually holds a whole /64 ([section 17](#17-remaining-limitations)). Publish an AAAA record for the domain only with `true` |
| `ATLAS_DEPLOY_SECURITY_REVIEW` | none | required by `public-demo`: the completed review's reference |
| `ATLAS_DEPLOY_API_PORT` | 8000 | 1024-65535; the API always binds 127.0.0.1 (tunnels, verification, the monitor); nginx uses `/run/atlas/api.sock` |
| `ATLAS_DEPLOY_SIMULATION_TIMEOUT_SECONDS` | 10 | 1-60 |
| `ATLAS_DEPLOY_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ATLAS_DEPLOY_API_DOCS` | `false` | Swagger UI (loads CDN scripts): tunnel profile only |

**Profiles** (private first):

| | `tunnel` | `private` | `public-demo` |
|---|---|---|---|
| Exposure | nothing; SSH tunnel | HTTPS for one domain | HTTPS for one domain |
| Authentication | SSH | HTTP Basic on **every** route, health included | none |
| Routes | the API on loopback (and the dashboard, M7) | every operation of the contract, and the dashboard (M7) | precomputed GET endpoints (but `/api/v1/system`), `/health/live` and the read-only dashboard (M7) only |
| Simulations | with the key | yes; nginx adds the key; 30/min per client (`low`: 12/min) | **not routed** |
| Operational detail (`/health/ready`, `/api/v1/system`), docs | yes / opt-in | yes; docs never | not routed |
| Firewall | 22 | 22, 80, 443 | 22, 80, 443 |
| Allowed without further review | yes | yes (phase B approval) | **no**: refused without `ATLAS_DEPLOY_SECURITY_REVIEW` |

Access matrix (the API's operations from `docs/api/openapi.json`, the dashboard's paths from
the release's verified build manifest (M7); any other path is a JSON 404, answered after
the allow-list, authentication and rate limits, so 401 without an account; a wrong method
on a route is a JSON 405). `HEAD` is passed on only where the API answers it, on
`/health/live` and `/health/ready` (for uptime checkers); on every other operation nginx
answers it with its JSON 405, as the API would (M8):

| Operation | Class | private | public-demo |
|---|---|---|---|
| `GET`/`HEAD` `/health/live` | liveness | account | open |
| `GET`/`HEAD` `/health/ready`, `GET /api/v1/system` | readiness (operational detail: component details, workers, limits, versions) | account | 404 |
| `GET /api/v1/models`, `/forecasts`, `/forecasts/history`, `/forecasts/metrics`, `/uncertainty/status`, `/uncertainty/intervals`, `/scenarios`, `/scenarios/comparison`, `/scenarios/{id}`, `/simulations/parameters` | read (precomputed) | account | open |
| `POST /api/v1/simulations` | compute | account + compute limit; nginx adds the key | 404 |
| `/docs`, `/redoc`, `/openapi.json` | documentation | 404 | 404 |
| `GET`/`HEAD` `/` and the other dashboard pages, `/assets/<exact name>`, `/favicon.svg`, `/third-party-licences.txt`, `/inter-licence.txt`, `/atlas-deployment.json` | dashboard (M7, static: the pages, assets, favicon and licence notices in their own limit `atlas_static`) | account | open (the descriptor says simulations are disabled) |

Do not assume the M5 simulation key protects the GET endpoints: it does not. In the private
profile nginx protects them; in the tunnel profile only SSH reaches them.

The rendered API environment (`/etc/atlas/atlas.env`) sets `ATLAS_API_ALLOWED_HOSTS` (the
domain, in an exposed profile, and loopback), the workers, the deadline, the body limit
(`ATLAS_API_MAX_BODY_BYTES=16384`), `ATLAS_API_DOCS` and the log level. CORS stays off (a
dashboard is meant to be same-origin). The key reaches the API as a file,
`ATLAS_API_KEY_FILE=%d/api-key` (new in M6: the API refuses a key file that others, or a
group other than root, could read; a systemd credential shows as 0440 group root, its ACL).

Validate before applying any change: `sudo atlas-deploy validate --installed`; apply with
`sudo atlas-deploy activate <current release id>` (renders, checks nginx, reloads systemd,
restarts, verifies). If the new configuration fails, the **last verified state** comes back:
the same release with the files rendered from the configuration it was verified with
(`deploy.env` keeps your edit: fix it and activate again). `atlas-deploy render` prints the
files without installing anything. `atlas-deploy render --apply` (used for the ACME site in
[section 7](#7-https-setup)) installs the files of the **current** release only, under the
deployment lock, and records itself in `history.jsonl` (`"action": "render"`); it reloads
nothing (it prints the commands to run). Before the first activation (no release is
current) it accepts the ACME stage of an installed release (`--stage acme`): that site
serves only ACME challenges, nothing from any release, and the first activation in the
private or public-demo profile needs the certificate it obtains. Another release, or the
complete site before the first activation, is refused with the command that serves it,
`atlas-deploy activate <id>` (which switches, restarts, verifies and restores the previous
state on failure), and so is any change while an activation is unfinished (M8).
Returning to the `tunnel` profile takes the ATLAS site out of nginx (its `sites-enabled` link
and its file are removed, `nginx -t`, then nginx is reloaded if it runs) before the switch;
if nginx refuses the change, everything is put back and nothing else changes. Close ports 80
and 443 in ufw afterwards (the tooling never touches the firewall).

## 7. HTTPS setup

Private profile, with Let's Encrypt (HTTP-01). Requires a DNS record for the domain pointing
to the server and ports 80 and 443 open.

```bash
# 1. deploy.env: the profile and the domain
sudo sed -i 's/^ATLAS_DEPLOY_PROFILE=.*/ATLAS_DEPLOY_PROFILE=private/' /etc/atlas/deploy.env
echo 'ATLAS_DEPLOY_DOMAIN=atlas.example.org' | sudo tee -a /etc/atlas/deploy.env

# 2. a temporary site that serves only ACME challenges (no proxying, no TLS yet)
sudo atlas-deploy render --stage acme --apply && sudo systemctl daemon-reload \
  && sudo nginx -t && sudo systemctl reload nginx

# 3. the certificate (certbot from Ubuntu; its timer renews it)
sudo apt-get install -y certbot
sudo certbot certonly --webroot -w /var/www/atlas-acme -d atlas.example.org \
     --deploy-hook 'systemctl reload nginx'

# 4. accounts (a generated 32-character password is shown once: store it in a password manager)
sudo atlas-deploy secrets add-user alice --generate

# 5. the complete site: renders it, nginx -t, restart, verify
sudo atlas-deploy activate "$(readlink /opt/atlas/current | cut -d/ -f2)"
curl -su alice https://atlas.example.org/health/ready
```

What the site enforces: TLS 1.2 and 1.3 with forward-secret AEAD ciphers only, no session
tickets, HSTS (no `includeSubDomains`, no preload); no certificate for unknown names
(`ssl_reject_handshake`) and no answer to a request whose Host is another name; port 80 only
serves ACME challenges and redirects to HTTPS. HTTP/2 is not enabled (HTTP/1.1 is enough for
this API and keeps the attack surface small). OCSP stapling is not configured (Let's
Encrypt stopped OCSP in 2025).

Renewal: `systemctl list-timers certbot.timer`, `sudo certbot renew --dry-run`. The monitor
warns 21 days and alerts 7 days before expiry. HSTS means browsers refuse plain HTTP for a
year: keep HTTPS working.

Servers that must not open port 80 can use a DNS-01 challenge (certbot DNS plugins) instead.

## 8. Starting, stopping and checking health

```bash
sudo systemctl status atlas         # state, "Status: ready: 7 components ready", memory, tasks
sudo systemctl stop atlas           # graceful: in-flight requests finish (up to 10 s), workers stop
sudo systemctl start atlas          # returns when the API is ready (Type=notify) or failed
sudo systemctl restart atlas
sudo atlas-deploy verify            # the full post-deployment verification (below)
sudo atlas-deploy status            # current release, its manifest, service state, history
curl -s http://127.0.0.1:8000/health/ready | python3 -m json.tool
journalctl -u atlas -f              # API log (JSON lines)
journalctl -t atlas-monitor -n 5 -o cat   # the last monitor reports (JSON)
```

The service starts at boot (`WantedBy=multi-user.target`); the monitor timer runs from the
first activation on, and at every boot.

`atlas-deploy verify` (run automatically by every activation) checks, and names every failure:

1. liveness: `/health/live` answers within 90 s;
2. readiness: `/health/ready` is 200 with every component ready, or lists what is not;
3. identity: the served ATLAS version and data fingerprint are the release's, and the
   service's main process runs from the release directory;
4. the simulation workers configured are alive; the API requires the simulation key;
5. scientific integrity on `/api/v1/system`, `/uncertainty/status` and `/scenarios`: the
   committed M3 status (`failed_calibration`), not decision-grade, M4 values
   `HYPOTHETICAL`, `validated_recommendation` and `point_forecast_validated` false;
6. access control: a simulation without the key is refused (401); with it, a 7-day smoke
   simulation answers 200 with its HYPOTHETICAL label. The key is sent only if the socket
   listening on the API port belongs to the service's main process and the request without
   it was refused;
7. exposure: the API listens on loopback addresses only;
8. the proxy socket: `/run/atlas/api.sock` answers `/health/live` (nginx's way in);
9. (M7) the dashboard, for a release that has one: the API serves `/` as the recorded
   `index.html` with the Content-Security-Policy the release records, and the deployment
   descriptor in the format it records; in the private and public-demo profiles, the copy
   published for nginx (`/opt/atlas/dashboard/<release id>/`) holds exactly the recorded
   files.

Every answer is treated as untrusted: a malformed body fails a check, not the verification.
Verification reaches the API directly and reads the published copy: it does not see what
nginx has loaded. That is why every activation, rollback and recovery reloads nginx (in the
private and public-demo profiles) before it verifies, and why a site installed by hand is
not verified.

## 9. Troubleshooting

| Symptom | Look | Usual cause | Fix |
|---|---|---|---|
| `atlas.service` failed, `status=2/INVALIDARGUMENT` | `journalctl -u atlas -n 30` | invalid setting (`atlas: error: ...`), a key file with unsafe permissions | fix `deploy.env`, `atlas-deploy validate --installed`, `activate <current>`; exit 2 is never restarted |
| `status=243/CREDENTIALS` | `journalctl -u atlas -n 30` | `/etc/atlas/credentials/api-key` missing | `atlas-deploy secrets init`; restarted until the start limit, then see the next row |
| `start-limit-hit` | `systemctl status atlas` | 6 failed starts in 15 min | fix the cause, then `systemctl reset-failed atlas && systemctl start atlas` |
| readiness 503 naming a component | `curl -s 127.0.0.1:8000/health/ready` | a release file damaged or missing (`file_altered`, `depends on: ...`) | `atlas-deploy manifest check /opt/atlas/current`; roll back or rebuild |
| browser 401 | nginx access log `"status":401` | wrong or missing account | `atlas-deploy secrets add-user NAME --generate`, `systemctl reload nginx` |
| 429 `rate_limited` | access log | the client exceeds 10 req/s or 30 simulations/min (`low`: 12/min) | slow the client; limits are per address (IPv6, off unless `ATLAS_DEPLOY_IPV6=true`: per address too, so one /64 counts as many clients) |
| 503 `upstream_unavailable` | `systemctl status atlas` | the API is down or restarting | see the first rows |
| 503 `simulations_busy` | `/api/v1/system` workers | every worker busy | retry after `Retry-After`; `standard` profile on 2 vCPU |
| 504 `simulation_timeout` | `journalctl -u atlas \| grep simulation_timeout` | a slow CPU (shared vCPU steal) | `ATLAS_DEPLOY_SIMULATION_TIMEOUT_SECONDS=20` |
| restarts with "Watchdog timeout" | `journalctl -u atlas` | the event loop stalled 30 s (swapping, CPU starvation) | check memory and swap; `low` profile |
| memory near the limit | `systemctl show atlas -p MemoryCurrent` | many large responses, worker growth | `low` profile, more memory |
| `nginx -t` fails after `render --apply` | `sudo nginx -t` | another site claims `default_server`, missing certificate | remove the other default site; finish section 7 |
| certificate not renewed | `sudo certbot renew --dry-run` | port 80 closed, DNS changed | open 80, fix DNS |
| an activation stopped half-way (SSH lost, Ctrl-C) | `atlas-deploy status` shows `pending_activation`; other changes are refused ("run `atlas-deploy recover` first") | interrupted | `sudo atlas-deploy recover` (it completes or undoes the activation: [section 11](#11-rolling-back-and-recovering)) |
| `render --apply`: "is not the current release" | `atlas-deploy status` | `--release-dir` names another release than `current` | `sudo atlas-deploy activate <id>` to serve that release |
| `render --apply`: "no release is active" | `atlas-deploy status` | the complete site requested before the first activation | the ACME site first if the certificate is missing (`render --stage acme --apply`, [section 7](#7-https-setup)), then `sudo atlas-deploy activate <id>` |
| service stopped and disabled after an activation (`failed_and_stopped`, `rollback_failed`) | `atlas-deploy status`, the record's `detail` | no verified state to return to, or it failed too: nothing unverified serves, even after a reboot | fix the cause, then `atlas-deploy activate <id>` (it enables the service again) |
| `a release build is running` from `prune` | `ps aux \| grep build-release` | a build holds its lock | prune after it finishes |
| `another deployment holds the lock` | `ps aux \| grep atlas-deploy` | a deployment is running | wait; never delete the lock while one runs |

Run long operations inside `tmux` or `screen` so an SSH disconnect does not interrupt them.

## 10. Upgrading

```bash
cd ~/atlas && git fetch && git checkout <new-commit>           # for the scripts only
sudo deploy/scripts/build-release.sh <new-commit>              # builds and checks; serves nothing
sudo atlas-deploy plan-upgrade 1.0.1-<sha12>                   # what would change (read-only)
sudo atlas-deploy activate 1.0.1-<sha12>                       # restores the verified state on failure
sudo atlas-deploy prune --keep 3                               # frees disk: nothing prunes by itself
```

`plan-upgrade` lists the version and commit change, the Python version, whether the lock
changed and which runtime packages, whether the API contract changed (then the nginx routes
change too), **which research artifacts differ** (expected: none, unless a milestone changed
them on purpose), the host files that would be rewritten, blocking problems and free disk.

The release manifest (`release.json`, `atlas-deploy manifest show <dir>`) identifies:
the git commit and ATLAS version; the Python version; the SHA-256 of `uv.lock` and every
locked runtime package; the snapshot (`legacy-v0`), its manifest's SHA-256 and the data
fingerprint (`5ab8b3a5...`); the SHA-256 of every research artifact (`snapshots/`, `01 Data/`,
`docs/results/`, `configs/`) and of every other file, and a fingerprint of the environment;
the API contract's SHA-256; the scientific status (`failed_calibration`, `HYPOTHETICAL`,
`validated_recommendation: false`); the build time, uv version and checks passed.

The environment fingerprint covers every file and link of the release's `.venv` except
bytecode and, from 1.0.0 (`environment_fingerprint_version: 2`), the two files uv rewrites
at every build of the project itself: its `uv_cache.json` (the project's file times at the
build) and the `RECORD` that lists it. Two builds of one commit with the same lock, uv,
Python (patch version included), platform (the architecture and C library, which select
the wheels uv installs) and path are therefore expected to share it; two servers share it
only if they also match in all of these. Measured on one x86-64 machine only: two installs
of commit 8f077ed at one path, with the build's uv settings (`uv sync --locked --no-dev`
with uv 0.11.32, copy mode, compiled bytecode, `/usr/bin/python3.12`), held 4,390 regular
files besides bytecode each and differed in those two only: counted, they gave two
different fingerprints; left out, the same one. A manifest written before 1.0.0 has no
fingerprint version: its fingerprint counts every file and is checked that way.

The manifest also records the dashboard (M7): its version, every file's SHA-256 and the
build manifest's SHA-256; `plan-upgrade` says whether it changes. From 1.0.0 it also
records what the release's code sends with its dashboard: its response headers
(Content-Security-Policy, Permissions-Policy, ...) and the deployment descriptor of each
exposed profile, with its format. Whatever tool activates, restores or rolls back to the
release renders the dashboard part of its nginx site from that record and verifies the
API's Content-Security-Policy and descriptor format against it, so an older release is
served and verified with its own policy. A release built by 0.7.0 records only its
Content-Security-Policy; its other headers and its descriptors are taken from the tool,
which are the same as in 0.7.0 until a release changes them.

**From 0.7.0 (M7) to 1.0.0**, plan and activate with the new release's own tool, as from M6
to M7: `sudo /opt/atlas/releases/<new id>/.venv/bin/atlas-deploy plan-upgrade <new id>`,
then `... activate <new id>`. The 0.7.0 tool fingerprints every file of the environment, so
it reports a 1.0.0 release as "the installed environment (.venv) differs from the
manifest" and refuses it before changing anything. Restoring or rolling back to a 0.7.0
release with the 1.0.0 tool works (its manifest is checked as it was written).

**From M6 to M7**, plan and activate with the new release's own tool, in every profile:
`sudo /opt/atlas/releases/<new id>/.venv/bin/atlas-deploy plan-upgrade <new id>`, then
`... activate <new id>`. In the private and public-demo profiles the M6 tool cannot render
the M7 site template (it has new placeholders): its `plan-upgrade` fails with a
`RenderError`, and its `activate` refuses before changing anything. In the tunnel profile
(no nginx site) the M6 tool plans and activates the M7 release, but never runs the M7 check
that the API serves the dashboard. The first activation in an exposed profile creates
`/opt/atlas/dashboard` (root:www-data 0750) if the bootstrap was run from M6; nothing needs
to be prepared by hand.
Rolling back to an M6 release with the M7 tool works (an M6 release has no dashboard to
publish).

The downtime of an activation is the restart: about 3-6 s. The old release keeps serving
while the new one is built and checked.

## 11. Rolling back and recovering

**Automatic.** Every activation that verifies is kept as the **last verified state**: the
release and the `deploy.env` it was verified with (`/var/lib/atlas/deploy/last-good.json`).
An activation whose service does not start, that fails verification or that meets any
unexpected error after the switch returns to that state: its release, with the files
rendered from its configuration, restarted and verified again. So a failed upgrade returns
to the previous verified release, and a failed configuration change on the same release
returns to the previous configuration. A release that never verified is never restored. If
the new files are refused before the switch (nginx, systemd, a write error), nothing is
switched and the previous files are put back. With no verified state to return to (a
first deployment that fails), or if the restoration fails too, the service is **stopped and
disabled**, so nothing unverified serves, not even after a reboot. Every step is in
`/var/lib/atlas/deploy/history.jsonl`; every activation's `in_progress` record and its
terminal record (`activated`, `refused`, `rolled_back`, `rollback_failed` or
`failed_and_stopped`) share an operation id. An activation without its terminal record
(interrupted) blocks every other change until `atlas-deploy recover` completes it.

**Recovering.** An activation can be interrupted (a lost SSH session, Ctrl-C) after the
switch, before or while the service restarts. If the restart was issued, systemd finishes
it and the API comes up on the new release; if not, the previous release keeps running.
Either way nginx has not been reloaded and still serves the previous release's site (its
dashboard, routes and access rules). `atlas-deploy recover` therefore completes the
activation rather than only checking it: if `current` already points to the interrupted
release and that release passes its checks, it repeats the activation's steps after the
switch. It installs the release's files again (idempotent: the published dashboard, units,
environment and site, `nginx -t`, systemd reloaded), restarts the service, reloads nginx
(private and public-demo profiles) and verifies; only then is the release, with its
`deploy.env`, kept as the last verified state (`activated`). The restart is not skipped
when the release already runs: it is what loads the environment just installed (from a
`deploy.env` you may have fixed since), and nginx loads the new site only once the
service runs the release. Otherwise (the activation stopped before the switch, a check,
the start or the verification fails) it restores the last verified state as above
(`rolled_back`). Until 1.0.0, `recover` only verified, and could keep a release as verified
while nginx still served the previous one (M8).

**Manual.**

```bash
sudo atlas-deploy rollback                 # the release active before the current one
sudo atlas-deploy rollback --to <release id>
sudo atlas-deploy recover                  # after an interrupted activation
```

**By hand**, if the tooling itself cannot run (it uses only the standard library, so this
should not happen):

```bash
cd /opt/atlas && sudo ln -sfn releases/<good-id> current.new && sudo mv -T current.new current
sudo systemctl restart atlas && curl -s http://127.0.0.1:8000/health/ready
```

This restores the API only: `atlas.env`, the units and the nginx site stay as rendered for
the newer release, so in an exposed profile nginx keeps its routes and serves that release's
dashboard (`/opt/atlas/dashboard/<newer id>`, M7) next to the older API. As soon as the
tooling runs again, `sudo atlas-deploy rollback --to <good-id>` re-renders and verifies them.

An explicit rollback (`atlas-deploy rollback`) restores the code, environment and research
artifacts of the chosen release, with the current `deploy.env` re-rendered from that
release's templates. The nginx site of the restored release points at that release's
dashboard copy, so the browser always gets the dashboard built with the API it talks to
(M7), with the headers and deployment descriptor that release records, and verification
expects the Content-Security-Policy and descriptor format that release records
([section 10](#10-upgrading)). So a newer tool (the one that ran a failed upgrade, or the
current release's after an upgrade) restores and rolls back to an older release even when
the dashboard's policy changed between them; until 1.0.0 the tool's own policy was used,
and such a restoration failed its dashboard check (M8). No release is ever modified or
deleted by an activation, so a failed deployment cannot alter the research outputs of any
release; `prune` never removes the current release, the rollback target or the last
verified state's release, and waits for a running build.

## 12. Backups and recovery

ATLAS keeps no user data and no database: everything it serves is in the repository. Back
up only what cannot be rebuilt:

| What | Why |
|---|---|
| `/etc/atlas/` (with `credentials/`) | configuration and the simulation key |
| `/etc/nginx/atlas/` | accounts and nginx's copy of the key |
| `/var/lib/atlas/deploy/history.jsonl`, `last-good.json` | which commits were deployed when; the last verified configuration |
| `/etc/letsencrypt/` (optional) | certificates (can be re-issued) |

```bash
sudo tar -C / -czf - etc/atlas etc/nginx/atlas var/lib/atlas/deploy/history.jsonl \
  var/lib/atlas/deploy/last-good.json \
  | gpg --symmetric --cipher-algo AES256 -o atlas-config-$(date +%F).tar.gz.gpg
```

Store the encrypted archive off the server. Releases need no backup: they are rebuilt from
the commit recorded in the history.

**Recovery onto a new server:** preflight; harden (5.1); bootstrap (5.2); restore the archive
(`gpg -d ... | sudo tar -C / -xzf -`); build the last good commit from the history (5.3).
Tunnel profile: `atlas-deploy validate --installed`, then `atlas-deploy activate <id>`.
Private profile: point DNS at the new server first; if the certificate was not restored,
activate in the tunnel profile, then follow section 7 steps 1-5 (the certificate needs DNS
and the ACME site before the private site can be activated).

**Rotating the simulation key** (after a suspected leak, or periodically): `sudo
atlas-deploy secrets rotate`, then `sudo atlas-deploy activate <current release id>` (restarts
the API with the new credential and reloads nginx with its copy). **Revoking an account**:
`sudo atlas-deploy secrets remove-user NAME && sudo systemctl reload nginx`; replacing a
password is `secrets add-user NAME --generate` again.

## 13. Security considerations

| Concern | Control | Residual risk |
|---|---|---|
| Unauthorised exposure | private-first profiles; the API binds 127.0.0.1 only (fixed in the unit) and systemd denies it any non-loopback address (`IPAddressDeny=any`); nginx reaches it through a Unix socket in the service's own runtime directory, so no other local account can stand in for it (and receive the key nginx adds) while it restarts; nginx routes only the contract's operations and the exact files of the release's verified dashboard build (M7); returning to the tunnel profile removes the nginx site; ufw default deny; public-demo refused without a review and never shows operational detail | a local user on the server can reach the API on loopback: GET endpoints serve committed public research outputs; simulations need the key; while the API is down another local account could listen on port 8000 (only an operator sending the key there by hand is exposed: `verify` and nginx never are) |
| Authentication | HTTP Basic over TLS on every route; SHA-512-crypt hashes (weak schemes refused); generated 192-bit passwords, chosen ones at least 20 characters; optional network allow-list | no MFA, no lockout (per-client rate limit instead: 10 requests/s on the API, 20 on the dashboard's files (M7), separate zones that add up to 30 per client, counted before authentication); prefer `ALLOW_CIDRS` or a VPN for stronger protection |
| Secret leakage | the key is generated on the server, root-only, delivered as a systemd credential; never in the environment, the unit, `systemctl show`, logs, the repository or command output (tested); passwords go through stdin to openssl | root can read every secret (by design) |
| Privilege escalation | user `atlas` without shell, home or capabilities; `NoNewPrivileges`, empty capability set, syscall filter, user namespace; releases root-owned and read-only; activation refuses a release with any file writable by group or others, set-id bits or links outside it; build checks run with `setpriv --no-new-privs` | root runs `atlas-deploy` from a root-owned release (no writable path in between) |
| Proxy mistakes | a header allow-list: no client header is passed on but `Content-Type` and `Accept` (so `Authorization`, `Cookie`, every `X-Forwarded-*`, `Forwarded`, `X-Real-IP`, `Proxy-Authorization` and client keys never reach the API); a fixed upstream Host; the key added on simulations only; nginx's request id passed and logged; security headers set once (the API's copies hidden); encoded slashes, backslashes, dots, line breaks and NUL, empty and dot segments and fragments refused (JSON 400, unparsable request lines included); unknown paths answered after authentication and rate limits | path normalisation differences are neutralised by the allow-list and method rules, not eliminated; a wrong method (405) and an ambiguous path (400) are answered before authentication (they reveal only the public contract) |
| Resource exhaustion | per-client request and connection limits, a tighter simulation limit, 16 KiB bodies, 10 s header and body deadlines, no request or response spooling to disk; the API's own limits (in-flight cap, workers without a queue, deadlines); MemoryHigh/Max, TasksMax, CPUWeight and IOWeight below sshd's so administration stays possible | a distributed attack can exhaust a small server: use the provider's DDoS protection or a CDN before any public exposure |
| Logs | API: JSON with whitelisted fields, no bodies, queries, headers or client addresses; nginx: JSON with the path (no query), status, timings, client address and user name; journal capped at 500 MB / 30 days, nginx logs 14 days (logrotate) | nginx logs client addresses (needed against abuse): personal data kept 14 days |
| Supply chain | the exact commit; `uv.lock` hashes for every wheel; uv itself hash-pinned; no Python download; Ubuntu packages from Ubuntu | commits are not signed: deploy only reviewed commits merged to `main` |
| Unsafe configurations | `deploy.env` validated strictly (no secrets, no shell syntax, ranges, profile rules); the rendered environment validated by the API's own parser; units checked by `systemd-analyze verify` and the site by `nginx -t` (tests); every contract path checked before it reaches nginx; activation refuses before switching, also when `/opt/atlas`, its releases directory, `/etc/atlas` or `/etc/nginx/atlas` could be replaced by another user, or the TLS private key is readable by others | — |
| Administrative access | SSH keys only (section 5.1), sudo; ATLAS has no administrative HTTP endpoint | the guide documents SSH hardening; the preflight reports it |

## 14. Monitoring and alerting

`atlas-monitor.timer` (started by the first activation) runs `atlas-deploy monitor` every minute (a sandboxed one-shot unit
under its own account `atlas-monitor`, separate from the service's so the API cannot alter
the monitor's state; no agent, no database, no outbound network). Each run writes one JSON
object to the journal:

```bash
journalctl -t atlas-monitor -o cat -n 1 | python3 -m json.tool
```

| Check | Warn | Critical |
|---|---|---|
| process availability | | `atlas.service` not active |
| restarts (`NRestarts`) | any since the last run | 3 or more within an hour (a slow crash loop) |
| liveness, readiness | readiness above 1 s | no answer; any component not ready |
| simulation workers | fewer alive than configured; new timeouts or lost workers | |
| service memory | above 90 % of `MemoryMax` | |
| server memory | below 10 % available | below 5 % |
| CPU | 5-minute load above twice the CPUs | |
| disk (`/`, `/opt/atlas`, `/var/log`) | below 15 % or 1 GiB free | below 5 % or 256 MiB |
| logs | API warnings or errors since the last run (by event) | |
| TLS (private, public-demo) | expiry within 21 days | invalid, or expiry within 7 days |

Metrics in each report: service state, memory and CPU use, latencies, worker counters,
available memory and swap, load per CPU, free disk, log counts, days of certificate left.

**Alerting.** A critical run exits 1 and systemd starts
`atlas-alert@atlas-monitor.service.service` (the instance is the failed unit's name), which
runs `/etc/atlas/alert-hook <unit>` if the operator installed it (executable). It runs as a
dynamic unprivileged user with access to the journal and the network, and otherwise the
monitor's sandbox (exposure 1.3). Options, from simplest:

- e-mail: `#!/bin/sh` + `journalctl -t atlas-monitor -n 1 -o cat | mail -s "ATLAS alert: $1" ops@example.org`
  (needs a mail transfer agent such as `msmtp-mta`);
- a webhook (ntfy, Slack, Matrix): `curl -fsS -d "ATLAS alert: $1" https://ntfy.example.org/atlas`;
  keep a token out of the script: `systemctl edit atlas-alert@.service` with
  `LoadCredential=token:/etc/atlas/credentials/alert-token`, read `$CREDENTIALS_DIRECTORY/token`;
- an external uptime check of `https://<domain>/health/live` (with an account) or a
  dead-man's switch (a heartbeat the hook stops sending) to notice a dead server, which no
  local monitor can report.

A larger observability stack (Prometheus, Grafana) is deliberately not part of M6.

## 15. Reliability design

| Need | How |
|---|---|
| Start at boot | `WantedBy=multi-user.target`; the monitor's timer too |
| Ready means ready | `Type=notify`: the API sends `READY=1` after loading and verifying the reference data, starting the workers and binding the socket; `systemctl start` waits for it (120 s) |
| Liveness | `WatchdogSec=30`: the event loop pings every 10 s; a hung loop is aborted and restarted |
| Controlled restarts | `Restart=on-failure` with a growing delay (2 s to 60 s, by the number of automatic restarts since the last deliberate start); at most 6 starts in 15 minutes, then the unit stays failed until an operator acts; exit status 2 (invalid configuration) is never restarted; three automatic restarts within an hour make the monitor critical. A deliberate restart by `atlas-deploy` (activate, rollback, recover) first clears the unit's failure state and restart counter (`systemctl reset-failed`), so several operator actions in a few minutes are never blocked by the limit meant for automatic restarts |
| Graceful shutdown | `KillMode=mixed`, SIGTERM to the server only: in-flight requests finish (up to 10 s), the workers finish their simulation and stop, and the server exits (uvicorn re-raises the SIGTERM it handled: exit status 15, which systemd records as a success). What is left once the server has exited (at most the multiprocessing resource tracker, which holds no state) is SIGKILLed at once; a server that has not exited after `TimeoutStopSec=30` is SIGKILLed with everything else |
| Lost workers | the M5 pool replaces a worker that dies or times out during a simulation; a worker that dies while idle (the OOM killer prefers workers: they raise their `oom_score_adj` 500 above the server's, to 500 under the unit) is replaced by the next readiness check, which the monitor runs every minute, and logged as `worker_died_idle`, so no request lands on a dead worker; `OOMPolicy=continue` keeps the service running meanwhile |
| Degraded data | per-component readiness (M5): a damaged artifact gives 503 on its endpoints, the rest serves; the monitor alerts |
| Version identification | release id = version + commit; `release.json`; `atlas-deploy status`; verification compares the running process's directory, version and data fingerprint with the manifest |
| Failure reporting | every activation, refusal, rollback and recovery is a JSON record with its reasons; `atlas-deploy` exits 0 on success, 1 when a deployment step failed or was refused, 2 for invalid arguments or configuration or when it cannot proceed (another deployment, a running build, a missing release), 3 for an internal error (a bug) |
| Partial failures | builds clean up after themselves; any failure before the switch (nginx or systemd refusing the files, a write error, a rendering error) restores the previous files and changes nothing else; any failure after it, an unexpected error included, restores the last verified state (release and configuration); an error in the restoration itself stops and disables the service; interrupted activations block other changes until `recover` completes them (the service restarted and nginx reloaded before the release is kept) or restores the verified state; a lock prevents concurrent deployments and `prune` waits for builds |
| Applied at once | every activation reloads systemd (units written by `render --apply` too), starts the monitor timer, and restarts journald when its limits changed |

## 16. Testing

| Test module | What it checks |
|---|---|
| `test_m6_preflight.py` (39) | fake servers (directory trees) accepted or refused with actions: distributions, architectures, x86-64-v2, memory and swap against the estimate, disk, ports, exposed listeners, software, SSH/ufw/updates/clock, load and competing services, lock wheel tags; JSON and text; the standalone script with `python -I -S` (standard library only); a real run changes nothing |
| `test_m6_config.py` (57) | `deploy.env`: defaults (IPv6 off), profiles, every unsafe value refused, shell syntax and secrets refused, all problems reported together; the rendered environment accepted by the API's own parser for every profile; workers vs CPUs; `ATLAS_API_KEY_FILE`: read, newline handling, open modes (group read for the root group only), empty, oversized, non-ASCII, both variables, missing, errors never echo the content |
| `test_m6_render.py` (33) | deterministic rendering; unsafe values and missing placeholders refused; the unit's least privilege, loopback bind, Unix socket in its runtime directory, sandbox, credential and restart policy; resource limits by profile; no secret rendered; the monitor and alert units; `systemd-analyze verify` (no unknown key); `systemd-analyze security --offline` exposure at most 1.5 for the service (0.9 measured), the monitor and the alert hook (1.3 each); routes equal the contract, `/api/v1/system` operational, HEAD only where the API answers it; nginx on IPv6 only when asked; unroutable or unsafe contract paths refused; the private and public-demo sites (header allow-list, socket upstream, catch-all after authentication); `nginx -t` on every profile and stage; atomic, idempotent writes; the site taken out |
| `test_m6_proxy.py` (39; M7 removed the `/` case) | the rendered site in a real nginx with TLS, its upstream a Unix socket as in production: authentication on every route, method and path allow-lists, ambiguous paths (line breaks, fragments included), unparsable request lines (JSON 400), unknown paths behind authentication and rate limits, the key added to simulations only and client keys dropped, only `Content-Type` and `Accept` of the client's headers passed on, a fixed Host, nginx's request id, security headers exactly once, 413, per-client and simulation rate limits (429 JSON), upstream down (503 JSON), unknown SNI and foreign Host (no answer), HTTP redirect and ACME, TLS 1.1 refused, the allow-list (403), the public-demo separation (no `/api/v1/system`), the access log without secrets or queries; the real API behind it (HEAD passed on exactly where the API answers it, refused by nginx elsewhere) |
| `test_m6_secrets.py` (20) | key generation, the nginx copy kept identical and repaired, rotation, open modes, links, foreign keys; users added, replaced, removed; openssl SHA-512-crypt verified; weak hashes refused; the CLI never prints the key; permission checks; release trees (writable files, set-id bits, escaping links including `..` after an allowed prefix, owners) |
| `test_m6_manifest.py` (16) | identification and fingerprints (the environment fingerprint the same for two builds of one commit, every other file, other distributions' `RECORD` included, still counted; manifests written before 1.0.0 checked as written); altered artifacts, lock, contract, code, environment, added and removed files refused; bytecode written later ignored, a planted source file not; manifest errors; the CLI; **every research artifact byte-identical to the M5 merge commit** |
| `test_m6_releases.py` (40) | activation (monitor timer started, journald applied, the verified state kept); private activation with nginx; the tunnel profile taking the site out (and put back if nginx refuses); automatic restoration after failed verification or a failed start, to the last *verified* release; a failed configuration change restoring the verified configuration; a failed first deployment stopped and disabled; a failed restoration reported, stopped and disabled; an error inside the restoration; unsafe releases, configurations, directories and TLS keys refused without touching the service; nginx refusal before the switch (no dangling link); the lock; rollback; an interrupted activation blocking other changes until recovered, and a recovery that has the service and nginx load the configuration it keeps as verified (the activation interrupted before or during the restart); pruning (waits for builds); status and upgrade plans; the CLI; **real API processes**: a broken release detected over HTTP and the previous one restored and verified |
| `test_m6_notify.py` (10) | the systemd notification protocol on a real socket; READY only after the bind, watchdog pings from the event loop, STOPPING; variables removed before the workers start; the readiness summary; workers' OOM preference on real processes |
| `test_m6_verify_monitor.py` (47) | verification against a real API (every check), explicit failures, a dead service, no proxy; each integrity label flipped one at a time (each one detected); malformed answers failing checks, never the verification; the key never sent to a listener that is not the service's or that does not require it; the proxy socket; every monitor finding and level, a slow crash loop critical; CPU deltas; state; the command; the real probes |
| `test_m6_scripts.py` (22) | shellcheck; root-script rules (strict mode, no tracing, nothing piped to a shell); the bootstrap is a dry run by default and never touches the firewall; the build installs the lock and checks as the service user; argument errors; the tooling imports the standard library only; the console script; CI |
| `test_m6_container.py` (22: 20 for M6, 2 added by M7; CI job `deploy-e2e`) | **end to end on a disposable Ubuntu 24.04 server with systemd as PID 1** (below) |

The container test runs the real package as an operator would: the bootstrap's dry run
(nothing changed), apply and idempotent re-run (owners and modes checked); a release build
(read-only tree, the atlas user cannot write it, others cannot read it, no `.git`,
idempotent re-run); an interrupted build (SIGTERM during the install) leaving nothing; the
first activation (readiness, verification, loopback only, exposure score, processes as
atlas, the monitor timer active, the Unix socket in place, the verified state kept); no secret in `systemctl show`, the journal, the environment files or any process
environment, and workers' OOM score; the private profile through nginx with TLS (401, 200,
a simulation, docs 404, the redirect, no password in nginx logs); `kill -9` of the server
(restarted); `kill -STOP` (the watchdog restarts it); a killed worker (replaced); an invalid
setting (exit 2, no restart loop, repaired by an activation); a stop during a simulation
(answered 200, a clean exit, no worker killed); a broken release (rolled back automatically); an
explicit rollback between two verified releases (a second release is built from an empty
commit on top of the first, the same tree under another release id, and activated;
`atlas-deploy rollback` then returns to the first: `current`, the site's dashboard root,
the published copy and `atlas-deploy verify`); an interrupted activation (other changes
refused until it is recovered); the monitor and the alert hook; a reboot (everything back
and verified); memory and tasks within the limits; a return to the tunnel profile (the
site gone, nginx still running).

M7 adds `test_m7_deploy.py`, `test_m7_proxy.py` and two container tests for the dashboard
([DASHBOARD.md](DASHBOARD.md) section 13), and changes two M6 tests where the dashboard is
now intended: `test_m6_render.py` counts the API locations before the dashboard's, and
`test_m6_proxy.py` no longer expects `/` to be a 404. The test nginx (`m6_helpers.py`) now
runs each site inside an http block with the distribution's types by extension, as on
Ubuntu.

M8 adds regression tests for the corrections of this guide's sections 6, 10 and 11: an
activation interrupted before or during the restart and completed by `recover`, the
service restarted and nginx loading the new release's site only once the service runs it
(`test_m7_deploy.py`), and its configuration (`test_m6_releases.py`); a later tool
restoring and rolling back to an older release with that release's dashboard policy (its
Content-Security-Policy, Permissions-Policy and descriptor), and recorded policies that
could alter the site refused (`test_m7_deploy.py`); HEAD where the API answers it
(`test_m6_render.py`, and against the real API in `test_m6_proxy.py`); the environment
fingerprint of two builds (`test_m6_manifest.py`); IPv6 off by default
(`test_m6_config.py`, `test_m6_render.py`); `render --apply` of the current release only
(and of the ACME site before the first activation), under the lock and recorded
(`test_m7_deploy.py`). The container test's explicit rollback now switches between two
releases (it re-activated the current one before).

All earlier tests (M0-M5) run unchanged except one line: the version literal in
`test_m5_endpoints.py` (now 1.0.0). CI runs the whole suite with
`ATLAS_REQUIRE_DEPLOY_TOOLS=1` (a missing nginx, openssl, shellcheck or systemd-analyze
fails instead of skipping), shellcheck, and the container job.

**What could not be tested without a real server**: a real Let's Encrypt issuance and
renewal (needs public DNS and port 80); ufw/iptables on a real interface (the tests never
touch a firewall); a real VM reboot (a container restart stands in); performance on a
shared 1-vCPU VPS with CPU steal; the OOM killer under real memory pressure with swap;
provider-specific CPU models and kernels; IPv6 on a public network; unattended upgrades
replacing nginx or Python under a running release; months of log rotation; mobile browsers
against the real certificate.

## 17. Remaining limitations

- **One server, one process.** No high availability: an upgrade restarts the API (3-6 s),
  a server failure is an outage until recovery (section 12).
- **OS libraries are not pinned.** Ubuntu security updates can change Python or OpenSSL
  under a running release. Rebuild the release after a `python3.12` update (the build
  re-verifies the M4 results on the new interpreter).
- **Basic authentication** is a shared-secret scheme: no MFA, no per-user roles. Everyone
  with an account can run simulations (rate-limited). Fine for a private research
  deployment; not for a public service.
- **No public exposure is approved.** The `public-demo` profile exists to keep a later demo
  separate from computation and operational detail; it needs a completed security review,
  DDoS protection and a decision on log retention first.
- **Local users** of the server can reach the API on loopback (simulations need the key),
  and could listen on port 8000 while the API is down (nginx and `verify` never send the key
  there). The server is meant to be dedicated to ATLAS with one administrator.
- **IPv6 rate limits** count each address: a client with a /64 counts as many clients.
  nginx therefore listens on IPv4 only by default (`ATLAS_DEPLOY_IPV6=false`); turn IPv6
  on only when IPv6 clients are needed.
- **Unknown paths are rate limited, wrong methods and ambiguous paths are not**: nginx
  answers those two before its limits (cheap answers revealing only the public contract).
- **Commits are not signed**; the build trusts GitHub for the commit content (its SHA is
  checked) and PyPI for the wheels (their hashes are checked).
- **Alerting needs an operator hook**; without it, alerts are journal entries.
- The deployment inherits every scientific limitation of M2-M5 (docs/API.md section 11).

## 18. Authorising phase B

Phase B (a real deployment) needs, in writing:

1. **Approval of this package** (the M6 phase A pull request reviewed; merging it is a
   separate decision).
2. **The server**: provider, plan (vCPU, memory, disk), region, Ubuntu 24.04 LTS image, an
   administrator account with SSH key access and sudo; confirmation that it is dedicated to
   ATLAS.
3. **The preflight report** from that server (`--json`, with `--probe-dependencies`): no
   `fail`; the warnings acknowledged; the resource profile chosen.
4. **The exposure decision**: tunnel only, or private. For private: the domain name, who
   controls its DNS, the certificate method (HTTP-01 with port 80 open, or DNS-01), the HSTS
   duration, and optionally the networks allowed (`ALLOW_CIDRS`) or a VPN.
5. **Permission to change the server**: run the bootstrap, enable ufw with the listed
   rules, harden SSH, install certbot, enable unattended upgrades, add swap.
6. **Accounts**: who gets one, and where generated passwords are stored (a password
   manager); who holds sudo.
7. **Monitoring**: the alert channel (e-mail, webhook, none) and an external uptime check.
8. **Data and logs**: acceptance of nginx logging client addresses for 14 days and the
   journal for 30 days.
9. **Backups**: where the encrypted configuration archive is stored.
10. **The commit to deploy**: merged to `main`, with CI green including `deploy-e2e`.
11. **A window and rollback criteria**: when, who watches, and that a failed verification is
    rolled back without further approval.
12. **Confirmation that nothing is presented as validated**: the deployment is described
    as research software (FAILED_CALIBRATION intervals, HYPOTHETICAL parameters).

Not part of phase B without its own approval: the public-demo profile, any change to the
research results, M7 and M8.

## 19. The dashboard (M7)

M6 was built so the dashboard could be added without changing its model; M7 adds it
([DASHBOARD.md](DASHBOARD.md)):

- **Same origin.** nginx serves the dashboard's pages and files next to the API, so CORS
  stays off and the browser's Basic-auth session also covers the API calls.
- **A static route class** (`atlas.deploy.routes.dashboard_routes`, rendered into the site
  after the API's locations): the pages (answered with `index.html` through `try_files`),
  each asset by its exact name, every other top-level file of the build by its exact name
  (the favicon and, since M8, the licence notices `/third-party-licences.txt` and
  `/inter-licence.txt`, sent as `text/plain; charset=utf-8`), and the deployment descriptor
  `/atlas-deployment.json` (written into the site; served through the same authentication).
  GET and HEAD only, `disable_symlinks on`; the pages, assets and other files have their own
  rate zone `atlas_static` (the descriptor counts against the API's read limit); a page path
  can never shadow an API route. Anything else is the existing JSON 404, and nginx's own
  answers are JSON whatever extension the path ends with (`types { }` at server level).
- **A separate Content-Security-Policy for HTML** (`script-src 'self'`, Trusted Types, no
  inline code; `atlas.dashboard.DASHBOARD_CSP`), set once; JSON keeps `default-src 'none'`.
  Text answers are gzip-compressed; hashed assets are cached as immutable.
- **Simulations from the browser** work in the private profile without exposing the key
  (nginx adds it); in public-demo the descriptor says they are disabled and the route does not
  exist. In the tunnel profile the API serves the dashboard itself and disables simulations
  in the browser (the API requires its key there).
- **Releases.** The build (`web/dist`, committed) is checked by `build-release.sh` and recorded
  in `release.json`; activation in an exposed profile copies it to
  `/opt/atlas/dashboard/<release id>/`, verifies the copy, then writes the site that points
  to it (a release with an altered build is refused before anything changes); `verify`
  checks that the API serves it with the policy the release records and that the copy is
  exactly the recorded files; rollback and recovery restore the release's site, with the
  headers and descriptor it records, and so its dashboard (M8,
  [section 11](#11-rolling-back-and-recovering)); `prune` removes copies of pruned
  releases. `render --apply` publishes only the current release, installed, whose build
  matches its `release.json`.
- **Tests**: `test_m7_deploy.py`, `test_m7_proxy.py` (real nginx), the browser tests against
  both profiles, and `test_m6_container.py` (the dashboard through nginx after a real
  activation; after the automatic rollback and the explicit rollback from a second release,
  the site's dashboard root, read from the site file).

## 20. Independent review

Three independent reviewers, each with its own copy of the package and no part in writing
it, attacked it before this pull request. Each reproduced a finding before reporting it
(a real nginx, the real API, the deployment code with fake hosts, the scripts, mutations of
the code). **Security** looked at unauthorised exposure, configuration weaknesses, privilege
escalation, secret leakage, the proxy and unsafe scripts. **Reliability and operations**
looked at crash recovery, rollback, interrupted operations, restart loops, resource use on
a small server and the scripts. **Documentation, scientific integrity and tests** followed
this guide against the code, checked that nothing overstates validity and that no research
artifact changed, and mutated the code to see whether the tests notice.

Every confirmed finding was fixed and is covered by a test, except where stated:

| Severity | Finding | Fix |
|---|---|---|
| major | Returning to the `tunnel` profile left the earlier nginx site enabled and serving (public-demo without authentication, or private) | the activation takes the site out, checks nginx and reloads it (put back if nginx refuses); the domain leaves the API's allowed hosts |
| major | The monitor timer was enabled but never started: no monitoring until a reboot | every activation starts it (container test asserts it) |
| major | Bytecode written by root into a release made it fail its own manifest; the next failed upgrade then stopped the service | bytecode is not part of a release's identity (the tree check still covers it); the build compiles the code; root runs set `PYTHONDONTWRITEBYTECODE` |
| major | A failure could restore a release that never verified (the previous link, a pending one); a failed restoration left the service running; a failed release started again at boot | the last verified state is restored; terminal failures stop **and disable** the service |
| major | A failed configuration change on the current release rolled the code back, or stopped it, with the new configuration | the verified configuration is kept with its release and restored |
| major | An interrupted activation was hidden by any later record (a refused typo, a prune) | operation ids; every change is refused until `recover` completes it |
| major | Errors inside the restoration, or a malformed API answer during verification, escaped with no outcome | the restoration is guarded (stop and disable); verification treats answers as untrusted |
| major | The example commit of section 5 was the M5 merge (no `atlas-deploy`): the build would fail | placeholders |
| major (test) | The per-client rate-limit test was flaky (a request after the burst found the bucket refilled) | asserts on a refused reply inside the burst |
| major (test) | Removing most integrity checks from `verify`, or the smoke simulation's label checks, went unnoticed | each label flipped one at a time; both mutants are now killed |
| minor | While the API restarted, a local account could listen on its loopback port and receive the key from nginx (and from `verify`) | nginx uses a Unix socket in the service's own runtime directory; `verify` sends the key only to the service's main process after a 401 |
| minor | public-demo exposed `/api/v1/system` (workers, limits, component details) | classified as operational detail: never public |
| minor | Unknown paths were answered before authentication and rate limits | answered after them (try_files); see the residual risks below |
| minor | Unparsable request lines got nginx's HTML 500 | an internal error location answers the JSON 400 |
| minor | `X-Forwarded-Proto`, `-Port`, `-Prefix`, `Proxy-Authorization` reached the API | a header allow-list: only `Content-Type` and `Accept` |
| minor | The alert hook's sandbox scored 8.1 (exposed) | the monitor's sandbox with network and journal: 1.3 |
| minor | A refused first exposure left a dangling `sites-enabled` link (nginx could not reload) | removed with the site |
| minor | `prune` could delete a build in progress; `recover` and `rollback` decided outside the lock | `prune` takes the build lock; decisions under the lock |
| minor | The journald limits applied only after a reboot | journald is restarted when they change |
| minor | A slow crash loop (below the start limit) only warned | three automatic restarts within an hour are critical |
| minor | Section 7 step 2 did not reload systemd, so the monitor kept checking no certificate | every activation reloads systemd; the step does too |
| minor | Guide errors: monitor reports read with `-u` (systemd's line), the recovery order for the private profile, key-file modes, forwarding headers, test counts, rate limits per profile, exit codes, the stop sequence, the key rotation and account revocation procedures missing | corrected (sections 7-16) |
| nit | Contract paths, a `..` after an allowed link prefix, `%0A`/`#` in paths, group-readable key files, `--repo` read as an option by git, the TLS key's mode, directories above the releases | all checked or refused |

**Accepted residual risks** (documented in sections 13 and 17): an operator who sends the
key by hand to port 8000 while the API is down could hand it to another local account; a
wrong method (405) or an ambiguous path (400) is answered before authentication and the
limits (both reveal only the public contract); IPv6 rate limits count each address; the
probe runs the downloaded packages with the caller's privileges (run it unprivileged).

**What the reviews confirmed**: no route without authentication in the private profile, no
simulation or readiness detail in public-demo; path tricks, smuggling, Host and SNI tricks
and header injection refused; no secret in the environment, the units, command lines, logs
or output; nothing writable by the service account that root reads or runs; bounded
restarts, clean builds, atomic switches, concurrent operations refused; no wording that
presents ATLAS as validated (FAILED_CALIBRATION, HYPOTHETICAL and "not a validated
staffing or resource-allocation system" everywhere); `git diff 3f48f20 -- docs/results
snapshots "01 Data" configs` is empty, and a test compares every research artifact with
the M5 merge commit byte for byte.
