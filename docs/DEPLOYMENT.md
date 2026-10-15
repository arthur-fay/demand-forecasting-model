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
> **What the deployed software is.** ATLAS serves research outputs. The forecast is an
> unvalidated point forecast; the M3 prediction intervals have the status
> **FAILED_CALIBRATION**; every M4 operational parameter is **HYPOTHETICAL**. The API says so
> on every response (`integrity` block, `validated_recommendation: false` fixed by the
> schema), and the deployment checks that it still does after every activation
> ([section 8](#8-checking-health)). **A deployed ATLAS is not a validated NYC staffing or
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
19. Readiness for M7
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
│   - per-client limits: 10 req/s (burst 40), simulations 30/min (burst 6), 16 conns │
│   - 16 KiB bodies, 10 s header/body deadlines, ambiguous paths refused (400)       │
│   - adds X-API-Key to POST /api/v1/simulations only; strips credentials, cookies   │
│       │                                                                            │
│       ▼ http://127.0.0.1:8000  (loopback only; systemd also denies other addresses)│
│  atlas.service: user atlas, sandboxed, Type=notify, watchdog 30 s                 │
│   uvicorn + FastAPI (the M5 API) ──pipes──► 1 or 2 spawned simulation workers     │
│   WorkingDirectory=/opt/atlas/current ─► releases/<version>-<commit12> (read-only)│
│   simulation key: systemd credential (LoadCredential), never in the environment  │
│                                                                                    │
│  atlas-monitor.timer (every minute) ─► atlas-monitor.service (DynamicUser)        │
│        └─ a critical finding ─► atlas-alert@.service (optional operator hook)      │
│  journald (persistent, capped)    certbot.timer (renewal ─► nginx reload)          │
└────────────────────────────────────────────────────────────────────────────────────┘
```

On disk (`src/atlas/deploy/layout.py`):

| Path | Owner, mode | Content |
|---|---|---|
| `/opt/atlas/releases/<id>/` | root:atlas, files 0440/0550 | one immutable release: the exported commit (no `.git`), its `.venv`, `release.json` |
| `/opt/atlas/current` | link | `releases/<id>`, replaced atomically on activation |
| `/opt/atlas/tools/uv/` | root, 0755 | the hash-pinned uv (0.11.32) used by release builds |
| `/etc/atlas/deploy.env` | root, 0644 | the deployment configuration ([section 6](#6-configuration)); no secret |
| `/etc/atlas/atlas.env` | root, 0644 | the API's environment, rendered from `deploy.env`; no secret |
| `/etc/atlas/credentials/api-key` | root, 0600 (dir 0700) | the simulation key |
| `/etc/nginx/atlas/htpasswd` | root:www-data, 0640 | proxy accounts (SHA-512-crypt) |
| `/etc/nginx/atlas/simulation-key.conf` | root, 0600 | the key nginx adds to simulations |
| `/etc/nginx/sites-available/atlas.conf` | root, 0644 | the rendered site (linked into `sites-enabled`) |
| `/etc/systemd/system/atlas*.service`, `.timer` | root, 0644 | the rendered units |
| `/etc/systemd/journald.conf.d/60-atlas.conf` | root, 0644 | persistent journal, 200-500 MB cap, 30 days |
| `/var/lib/atlas/deploy/` | root, 0700 | `history.jsonl` (every deployment step), locks |
| `/var/cache/atlas/uv/` | root, 0700 | download cache of release builds |
| `/var/www/atlas-acme/` | root, 0755 | ACME HTTP-01 challenge files |

A **release id** is `<version>-<first 12 hex digits of the commit>`, e.g. `0.6.0-3f48f2049d13`.

## 2. Deployment method and why

**Native systemd services, nginx from Ubuntu, no containers.** Evaluated for a small VPS:

| | Native systemd (chosen) | Docker / Compose | Podman (rootless) |
|---|---|---|---|
| Extra memory | none | dockerd + containerd: about 140 MiB resident (measured on the test machine: 95 + 48 MiB) | none (no daemon) |
| Isolation | systemd sandbox: no capabilities, read-only system, private /tmp and devices, user namespace, syscall filter, loopback-only IP firewall; `systemd-analyze security` exposure **0.9 (SAFE)** | namespaces + seccomp; default profile weaker than the unit's | similar to Docker |
| Firewall | ufw rules apply | published ports bypass ufw (Docker's own iptables chains): a classic accidental exposure | rules apply |
| Security updates | unattended-upgrades patches nginx, OpenSSL, Python | images must be rebuilt | images must be rebuilt |
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
| whole `atlas.service` cgroup, 2 workers, after load | 290-305 MiB | 475 MiB |
| nginx | 38 MiB with 4 workers (one per CPU); about 20 MiB on 2 vCPUs | 20 MiB |
| monitor run (once a minute, a few seconds) | about 25 MiB | 30 MiB |
| operating system | 250-350 MiB on a typical Ubuntu server (the preflight measures it) | measured |
| release build (while the old release serves) | uv, then `atlas check` and the M4 reproduction | +400 MiB |

So the service needs about 0.33 GiB with one worker and 0.45 GiB with two; with nginx, the
monitor and Ubuntu, 1 GB is enough for the `low` profile only with swap, and 2 GB gives the
`standard` profile ample headroom. The limits in the unit leave room above the measured use;
the workers raise their own OOM score, so under memory pressure the kernel kills a worker
(replaced by the pool, one simulation lost) before the server.

**Disk**: one release is about 320 MB (the export 17 MB, the environment about 300 MB); three
are kept. The uv cache is about 220 MB, the journal at most 500 MB, nginx logs 14 days.

**CPU and time**: GET endpoints answer in single-digit milliseconds; a simulation takes 0.2-0.4 s
(at worst about 1.5 s) of one CPU (docs/API.md section 9). A release build took 45 s on the
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
deletes the directory: the definitive "can the locked ATLAS dependencies execute here".

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
and remove the general rule.

**5.2 Bootstrap** (idempotent; a dry run unless `--apply`):

```bash
cd ~/atlas
sudo deploy/scripts/bootstrap-host.sh --resources standard            # read the plan
sudo deploy/scripts/bootstrap-host.sh --resources standard --apply    # do it
```

It runs the preflight (and stops on UNSUITABLE), installs `nginx python3.12 python3.12-venv
git openssl ca-certificates` (`--with-certbot` adds certbot), disables nginx's stock default
site, creates the system user `atlas` (no login shell, no home), the directories of section 1
with their owners and modes, a hash-pinned uv in `/opt/atlas/tools/uv` (pip
`--require-hashes`, the same pins as CI) and `/etc/atlas/deploy.env` from
`deploy/deploy.env.example` (tunnel profile; an existing file is never overwritten). It
never touches the firewall, SSH, DNS or certificates and creates no secret.

**5.3 Build a release** from an exact, reviewed commit (merged to `main`, CI green):

```bash
sudo deploy/scripts/build-release.sh 3f48f2049d13fb28c00fef21cecd8c68d25958a6
```

It fetches exactly that commit (`git fetch --depth 1 <url> <sha>`, then checks the SHA),
exports it without `.git` into `/opt/atlas/releases/<id>`, installs the locked runtime
dependencies (`uv sync --locked --no-dev`: every wheel checked against the hashes in
`uv.lock`; no development tools; no Python download), makes the tree root-owned and
read-only (group `atlas` may read; others nothing), then runs the release's own checks **as
the unprivileged `atlas` user** (`setpriv --no-new-privs`): `atlas check` (snapshot integrity,
validation, reproduction), `atlas optimize inspect` and `atlas optimize verify` (the committed
M4 reference run reproduced byte for byte on this machine; `--skip-reproduction` omits it),
and the API contract equal to `docs/api/openapi.json`. Only then is `release.json` written
([section 10](#10-upgrading)). A failed or interrupted build removes its directory; an
existing complete release is checked and kept.

**5.4 Secrets and first activation** (tunnel profile):

```bash
R=/opt/atlas/releases/0.6.0-3f48f2049d13/.venv/bin/atlas-deploy
sudo $R secrets init                 # the simulation key, root-only, never printed
sudo $R validate --installed         # deploy.env, rendered files, secrets, permissions
sudo $R activate 0.6.0-3f48f2049d13  # render, install, switch, restart, verify
```

From now on use `/opt/atlas/current/.venv/bin/atlas-deploy` (below simply `atlas-deploy`;
`sudo ln -s /opt/atlas/current/.venv/bin/atlas-deploy /usr/local/sbin/` makes it so).

Reach the tunnel deployment from your machine: `ssh -L 8000:127.0.0.1:8000 admin@server`, then
`http://localhost:8000/health/ready` and the API in a browser or `curl`. Simulations need the
key (`sudo cat /etc/atlas/credentials/api-key`, header `X-API-Key`).

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
| `ATLAS_DEPLOY_IPV6` | `true` | whether nginx listens on IPv6 |
| `ATLAS_DEPLOY_SECURITY_REVIEW` | none | required by `public-demo`: the completed review's reference |
| `ATLAS_DEPLOY_API_PORT` | 8000 | 1024-65535; the API always binds 127.0.0.1 |
| `ATLAS_DEPLOY_SIMULATION_TIMEOUT_SECONDS` | 10 | 1-60 |
| `ATLAS_DEPLOY_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `ATLAS_DEPLOY_API_DOCS` | `false` | Swagger UI (loads CDN scripts): tunnel profile only |

**Profiles** (private first):

| | `tunnel` | `private` | `public-demo` |
|---|---|---|---|
| Exposure | nothing; SSH tunnel | HTTPS for one domain | HTTPS for one domain |
| Authentication | SSH | HTTP Basic on **every** route, health included | none |
| Routes | the API on loopback | every operation of the contract | precomputed GET endpoints and `/health/live` only |
| Simulations | with the key | yes; nginx adds the key; 30/min per client | **not routed** |
| `/health/ready` details, docs | yes / opt-in | readiness yes; docs never | not routed |
| Firewall | 22 | 22, 80, 443 | 22, 80, 443 |
| Allowed without further review | yes | yes (phase B approval) | **no**: refused without `ATLAS_DEPLOY_SECURITY_REVIEW` |

Access matrix of the private profile (from `docs/api/openapi.json`; anything else is a JSON
404 at nginx, a wrong method a JSON 405):

| Operation | Class | private | public-demo |
|---|---|---|---|
| `GET /health/live` | liveness | account | open |
| `GET /health/ready` | readiness | account | 404 |
| `GET /api/v1/system`, `/models`, `/forecasts`, `/forecasts/history`, `/forecasts/metrics`, `/uncertainty/status`, `/uncertainty/intervals`, `/scenarios`, `/scenarios/comparison`, `/scenarios/{id}`, `/simulations/parameters` | read (precomputed) | account | open |
| `POST /api/v1/simulations` | compute | account + compute limit; nginx adds the key | 404 |
| `/docs`, `/redoc`, `/openapi.json` | documentation | 404 | 404 |

Do not assume the M5 simulation key protects the GET endpoints: it does not. In the private
profile nginx protects them; in the tunnel profile only SSH reaches them.

The rendered API environment (`/etc/atlas/atlas.env`) sets `ATLAS_API_ALLOWED_HOSTS` (the
domain and loopback), the workers, the deadline, `ATLAS_API_DOCS=false` and the log level.
CORS stays off (a dashboard is meant to be same-origin). The key reaches the API as a file,
`ATLAS_API_KEY_FILE=%d/api-key` (new in M6: the API refuses a key file other users could read).

Validate before applying any change: `sudo atlas-deploy validate --installed`; apply with
`sudo atlas-deploy activate <current release id>` (renders, checks nginx, restarts, verifies,
restores on failure). `atlas-deploy render` prints the files without installing anything.

## 7. HTTPS setup

Private profile, with Let's Encrypt (HTTP-01). Requires a DNS record for the domain pointing
to the server and ports 80 and 443 open.

```bash
# 1. deploy.env: the profile and the domain
sudo sed -i 's/^ATLAS_DEPLOY_PROFILE=.*/ATLAS_DEPLOY_PROFILE=private/' /etc/atlas/deploy.env
echo 'ATLAS_DEPLOY_DOMAIN=atlas.example.org' | sudo tee -a /etc/atlas/deploy.env

# 2. a temporary site that serves only ACME challenges (no proxying, no TLS yet)
sudo atlas-deploy render --stage acme --apply && sudo nginx -t && sudo systemctl reload nginx

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
journalctl -u atlas-monitor -n 5 -o cat   # the last monitor reports
```

The service starts at boot (`WantedBy=multi-user.target`), as does the monitor timer.

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
   simulation answers 200 with its HYPOTHETICAL label;
7. exposure: the API listens on loopback addresses only.

## 9. Troubleshooting

| Symptom | Look | Usual cause | Fix |
|---|---|---|---|
| `atlas.service` failed, `status=2/INVALIDARGUMENT` | `journalctl -u atlas -n 30` | invalid setting (`atlas: error: ...`), unreadable key file | fix `deploy.env`, `atlas-deploy validate --installed`, `activate <current>`; exit 2 is never restarted |
| `start-limit-hit` | `systemctl status atlas` | 6 failed starts in 15 min | fix the cause, then `systemctl reset-failed atlas && systemctl start atlas` |
| readiness 503 naming a component | `curl -s 127.0.0.1:8000/health/ready` | a release file damaged or missing (`file_altered`, `depends on: ...`) | `atlas-deploy manifest check /opt/atlas/current`; roll back or rebuild |
| browser 401 | nginx access log `"status":401` | wrong or missing account | `atlas-deploy secrets add-user NAME --generate`, `systemctl reload nginx` |
| 429 `rate_limited` | access log | the client exceeds 10 req/s or 30 simulations/min | slow the client; limits are per address |
| 503 `upstream_unavailable` | `systemctl status atlas` | the API is down or restarting | see the first rows |
| 503 `simulations_busy` | `/api/v1/system` workers | every worker busy | retry after `Retry-After`; `standard` profile on 2 vCPU |
| 504 `simulation_timeout` | `journalctl -u atlas \| grep simulation_timeout` | a slow CPU (shared vCPU steal) | `ATLAS_DEPLOY_SIMULATION_TIMEOUT_SECONDS=20` |
| restarts with "Watchdog timeout" | `journalctl -u atlas` | the event loop stalled 30 s (swapping, CPU starvation) | check memory and swap; `low` profile |
| memory near the limit | `systemctl show atlas -p MemoryCurrent` | many large responses, worker growth | `low` profile, more memory |
| `nginx -t` fails after `render --apply` | `sudo nginx -t` | another site claims `default_server`, missing certificate | remove the other default site; finish section 7 |
| certificate not renewed | `sudo certbot renew --dry-run` | port 80 closed, DNS changed | open 80, fix DNS |
| an activation stopped half-way (SSH lost) | `atlas-deploy status` shows `pending_activation` | interrupted | `sudo atlas-deploy recover` |
| `another deployment holds the lock` | `ps aux \| grep atlas-deploy` | a deployment is running | wait; never delete the lock while one runs |

Run long operations inside `tmux` or `screen` so an SSH disconnect does not interrupt them.

## 10. Upgrading

```bash
cd ~/atlas && git fetch && git checkout <new-commit>           # for the scripts only
sudo deploy/scripts/build-release.sh <new-commit>              # builds and checks; serves nothing
sudo atlas-deploy plan-upgrade 0.6.1-<sha12>                   # what would change (read-only)
sudo atlas-deploy activate 0.6.1-<sha12>                       # restores the old release on failure
sudo atlas-deploy prune --keep 3                               # optional
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

The downtime of an activation is the restart: about 3-6 s. The old release keeps serving
while the new one is built and checked.

## 11. Rolling back and recovering

**Automatic.** An activation whose service does not start, or that fails verification,
switches back to the previous release, renders its files, restarts it and verifies it again.
If nginx refuses the new site, nothing is switched at all. A first deployment that fails is
stopped. Every step is in `/var/lib/atlas/deploy/history.jsonl`.

**Manual.**

```bash
sudo atlas-deploy rollback                 # the release active before the current one
sudo atlas-deploy rollback --to 0.6.0-3f48f2049d13
sudo atlas-deploy recover                  # after an interrupted activation
```

**By hand**, if the tooling itself cannot run (it uses only the standard library, so this
should not happen):

```bash
cd /opt/atlas && sudo ln -sfn releases/<good-id> current.new && sudo mv -T current.new current
sudo systemctl restart atlas && curl -s http://127.0.0.1:8000/health/ready
```

Rollback restores the code, environment and research artifacts of the chosen release; the
configuration in `/etc/atlas` is shared and re-rendered from that release's templates. No
release is ever modified or deleted by an activation, so a failed deployment cannot alter
the research outputs of any release; `prune` never removes the current release or the
rollback target.

## 12. Backups and recovery

ATLAS keeps no user data and no database: everything it serves is in the repository. Back
up only what cannot be rebuilt:

| What | Why |
|---|---|
| `/etc/atlas/` (with `credentials/`) | configuration and the simulation key |
| `/etc/nginx/atlas/` | accounts and nginx's copy of the key |
| `/var/lib/atlas/deploy/history.jsonl` | which commits were deployed when |
| `/etc/letsencrypt/` (optional) | certificates (can be re-issued) |

```bash
sudo tar -C / -czf - etc/atlas etc/nginx/atlas var/lib/atlas/deploy/history.jsonl \
  | gpg --symmetric --cipher-algo AES256 -o atlas-config-$(date +%F).tar.gz.gpg
```

Store the encrypted archive off the server. Releases need no backup: they are rebuilt from
the commit recorded in the history.

**Recovery onto a new server:** preflight; harden (5.1); bootstrap (5.2); restore the archive
(`gpg -d ... | sudo tar -C / -xzf -`); build the last good commit from the history (5.3);
`atlas-deploy validate --installed`; `atlas-deploy activate <id>`; re-issue the certificate if
not restored (section 7); update DNS.

## 13. Security considerations

| Concern | Control | Residual risk |
|---|---|---|
| Unauthorised exposure | private-first profiles; the API binds 127.0.0.1 only (fixed in the unit) and systemd denies it any non-loopback address (`IPAddressDeny=any`); nginx routes only the contract's operations; ufw default deny; public-demo refused without a review | a local user on the server can reach the API on loopback: GET endpoints serve committed public research outputs; simulations need the key |
| Authentication | HTTP Basic over TLS on every route; SHA-512-crypt hashes (weak schemes refused); generated 192-bit passwords, chosen ones at least 20 characters; optional network allow-list | no MFA, no lockout (per-client rate limit instead); prefer `ALLOW_CIDRS` or a VPN for stronger protection |
| Secret leakage | the key is generated on the server, root-only, delivered as a systemd credential; never in the environment, the unit, `systemctl show`, logs, the repository or command output (tested); passwords go through stdin to openssl | root can read every secret (by design) |
| Privilege escalation | user `atlas` without shell, home or capabilities; `NoNewPrivileges`, empty capability set, syscall filter, user namespace; releases root-owned and read-only; activation refuses a release with any file writable by group or others, set-id bits or links outside it; build checks run with `setpriv --no-new-privs` | root runs `atlas-deploy` from a root-owned release (no writable path in between) |
| Proxy mistakes | fixed upstream Host; `Authorization`, `Cookie`, `X-Forwarded-*`, `Forwarded`, `X-Real-IP` never forwarded; client `X-API-Key` dropped or replaced; nginx's request id passed and logged; every location sets the full header set (nginx does not inherit `proxy_set_header`); security headers set once (the API's copies hidden); encoded slashes, dots and empty segments refused | path normalisation differences are neutralised by the allow-list and method rules, not eliminated |
| Resource exhaustion | per-client request and connection limits, a tighter simulation limit, 16 KiB bodies, 10 s header and body deadlines, no request or response spooling to disk; the API's own limits (in-flight cap, workers without a queue, deadlines); MemoryHigh/Max, TasksMax, CPUWeight and IOWeight below sshd's so administration stays possible | a distributed attack can exhaust a small server: use the provider's DDoS protection or a CDN before any public exposure |
| Logs | API: JSON with whitelisted fields, no bodies, queries, headers or client addresses; nginx: JSON with the path (no query), status, timings, client address and user name; journal capped at 500 MB / 30 days, nginx logs 14 days (logrotate) | nginx logs client addresses (needed against abuse): personal data kept 14 days |
| Supply chain | the exact commit; `uv.lock` hashes for every wheel; uv itself hash-pinned; no Python download; Ubuntu packages from Ubuntu | commits are not signed: deploy only reviewed commits merged to `main` |
| Unsafe configurations | `deploy.env` validated strictly (no secrets, no shell syntax, ranges, profile rules); the rendered environment validated by the API's own parser; units checked by `systemd-analyze verify` and the site by `nginx -t` (tests); activation refuses before switching | — |
| Administrative access | SSH keys only (section 5.1), sudo; ATLAS has no administrative HTTP endpoint | the guide documents SSH hardening; the preflight reports it |

## 14. Monitoring and alerting

`atlas-monitor.timer` runs `atlas-deploy monitor` every minute (a sandboxed `DynamicUser`
one-shot unit; no agent, no database, no outbound network). Each run writes one JSON
object to the journal:

```bash
journalctl -u atlas-monitor -o cat -n 1 | python3 -m json.tool
```

| Check | Warn | Critical |
|---|---|---|
| process availability | | `atlas.service` not active |
| restarts (`NRestarts`) | any since the last run | |
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

**Alerting.** A critical run exits 1 and systemd starts `atlas-alert@atlas-monitor.service`,
which runs `/etc/atlas/alert-hook <unit>` if the operator installed it (executable). It runs as a
dynamic unprivileged user with access to the journal. Options, from simplest:

- e-mail: `#!/bin/sh` + `journalctl -u atlas-monitor -n 1 -o cat | mail -s "ATLAS alert: $1" ops@example.org`
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
| Controlled restarts | `Restart=on-failure` with a growing delay (2 s to 60 s); at most 6 starts in 15 minutes, then the unit stays failed until an operator acts; exit status 2 (invalid configuration) is never restarted |
| Graceful shutdown | `KillMode=mixed`, SIGTERM to the server: in-flight requests finish (up to 10 s), the workers finish their simulation and stop; leftovers are killed after 30 s |
| Lost workers | the M5 pool replaces a dead or timed-out worker; `OOMPolicy=continue` keeps the service when the OOM killer takes a worker, which it prefers (workers set `oom_score_adj` 500) |
| Degraded data | per-component readiness (M5): a damaged artifact gives 503 on its endpoints, the rest serves; the monitor alerts |
| Version identification | release id = version + commit; `release.json`; `atlas-deploy status`; verification compares the running process's directory, version and data fingerprint with the manifest |
| Failure reporting | every activation, refusal, rollback and recovery is a JSON record with its reasons; `atlas-deploy` exits 1 on any failure |
| Partial failures | builds clean up after themselves; activation refuses before switching when the files are refused; interrupted activations are recovered (`recover`); a lock prevents concurrent deployments |

## 16. Testing

| Test module | What it checks |
|---|---|
| `test_m6_preflight.py` (38) | fake servers (directory trees) accepted or refused with actions: distributions, architectures, x86-64-v2, memory and swap against the estimate, disk, ports, exposed listeners, software, SSH/ufw/updates/clock, load and competing services, lock wheel tags; JSON and text; the standalone script with `python -I -S` (standard library only); a real run changes nothing |
| `test_m6_config.py` (56) | `deploy.env`: defaults, profiles, every unsafe value refused, shell syntax and secrets refused, all problems reported together; the rendered environment accepted by the API's own parser for every profile; workers vs CPUs; `ATLAS_API_KEY_FILE`: read, newline handling, open modes, empty, oversized, non-ASCII, both variables, missing, errors never echo the content |
| `test_m6_render.py` (29) | deterministic rendering; unsafe values and missing placeholders refused; the unit's least privilege, loopback bind, sandbox, credential and restart policy; resource limits by profile; no secret rendered; the monitor and alert units; `systemd-analyze verify` (no unknown key); `systemd-analyze security --offline` exposure at most 1.5 (0.9 measured); routes equal the contract; unroutable contracts refused; the private and public-demo sites; `nginx -t` on every profile and stage; atomic, idempotent writes |
| `test_m6_proxy.py` (32) | the rendered site in a real nginx with TLS: authentication on every route, method and path allow-lists, ambiguous paths, the key added to simulations only and client keys dropped, credentials and forwarding headers never forwarded, a fixed Host, nginx's request id, security headers exactly once, 413, per-client and simulation rate limits (429 JSON), upstream down (503 JSON), unknown SNI and foreign Host (no answer), HTTP redirect and ACME, TLS 1.1 refused, the allow-list (403), the public-demo separation, the access log without secrets or queries; the real API behind it |
| `test_m6_secrets.py` (24) | key generation, the nginx copy kept identical and repaired, rotation, open modes, links, foreign keys; users added, replaced, removed; openssl SHA-512-crypt verified; weak hashes refused; the CLI never prints the key; permission checks; release trees (writable files, set-id bits, escaping links, owners) |
| `test_m6_manifest.py` (11) | identification and fingerprints; altered artifacts, lock, contract, code, environment, added and removed files refused; manifest errors; the CLI; **every research artifact byte-identical to the M5 merge commit** |
| `test_m6_releases.py` (25) | activation; private activation with nginx; automatic restoration after failed verification or a failed start; a failed first deployment stopped; a failed restoration reported; unsafe releases and configurations refused without touching the service; nginx refusal before the switch; the lock; rollback; recovery of an interrupted activation; pruning; status and upgrade plans; the CLI; **real API processes**: a broken release detected over HTTP and the previous one restored and verified |
| `test_m6_notify.py` (9) | the systemd notification protocol on a real socket; READY only after the bind, watchdog pings from the event loop, STOPPING; variables removed before the workers start; the readiness summary; workers' OOM preference on real processes |
| `test_m6_verify_monitor.py` (28) | verification against a real API (every check), explicit failures, a dead service, no proxy; every monitor finding and level; CPU deltas; state; the command; the real probes |
| `test_m6_scripts.py` (22) | shellcheck; root-script rules (strict mode, no tracing, nothing piped to a shell); the bootstrap is a dry run by default and never touches the firewall; the build installs the lock and checks as the service user; argument errors; the tooling imports the standard library only; the console script; CI |
| `test_m6_container.py` (17, CI job `deploy-e2e`) | **end to end on a disposable Ubuntu 24.04 server with systemd as PID 1** (below) |

The container test runs the real package as an operator would: the bootstrap's dry run
(nothing changed), apply and idempotent re-run (owners and modes checked); a release build
(read-only tree, the atlas user cannot write it, others cannot read it, no `.git`,
idempotent re-run); an interrupted build (SIGTERM during the install) leaving nothing; the
first activation (readiness, verification, loopback only, exposure score, processes as
atlas); no secret in `systemctl show`, the journal, the environment files or any process
environment, and workers' OOM score; the private profile through nginx with TLS (401, 200,
a simulation, docs 404, the redirect, no password in nginx logs); `kill -9` of the server
(restarted); `kill -STOP` (the watchdog restarts it); a killed worker (replaced); an invalid
setting (exit 2, no restart loop, repaired by an activation); a stop during a simulation
(answered 200, clean exit, no SIGKILL); a broken release (rolled back automatically); an
explicit rollback; an interrupted activation (recovered); the monitor and the alert hook;
a reboot (everything back and verified); memory and tasks within the limits.

All earlier tests (M0-M5) run unchanged; two M5 files changed: the version literal in
`test_m5_endpoints.py` (0.6.0) and none other. CI runs the whole suite with
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
- **Local users** of the server can reach the API on loopback (simulations need the key).
  The server is meant to be dedicated to ATLAS with one administrator.
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

## 19. Readiness for M7

The deployment is ready for a dashboard (M7):
- **Same origin**: nginx can serve the dashboard's static files next to the API, so CORS
  stays off and the browser's Basic-auth session also covers the API calls.
- **Simulations from the browser** work in the private profile without exposing the key
  (nginx adds it); in public-demo they are not available.
- **The contract** stays `docs/api/openapi.json`; the routes follow it automatically.

M7 must add:
- a route class for the dashboard's static files in `atlas.deploy.routes` and the templates,
  with a **separate Content-Security-Policy** for HTML (`script-src 'self'`; the API's
  `default-src 'none'` stays on JSON);
- the built assets in the release (committed or built from a locked toolchain) and their
  fingerprints in the manifest;
- the integrity labels on every chart (docs/API.md section 12), and a layout usable on
  mobile browsers;
- an end-to-end check of the dashboard through nginx in `test_m6_container.py`.

Nothing in M6 blocks M7.

## 20. Independent review

See the pull request and the commit history: the review lenses, their findings and the
fixes are recorded there once the review is complete.
