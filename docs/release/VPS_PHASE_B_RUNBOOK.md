# M6 phase B runbook: deploying ATLAS 1.0 to the owner's VPS

**Status: not executed.** This runbook was prepared in M8. Nothing in it has been run against the
owner's server; no server has been inspected, and its suitability is unknown until the read-only
preflight (step 2) says otherwise. Every step that changes the server needs the owner's written
authorisation (the checklist in [DEPLOYMENT.md](../DEPLOYMENT.md) section 18).

It turns the M6 operations guide, [DEPLOYMENT.md](../DEPLOYMENT.md), into an ordered procedure for
one deployment of the release candidate, with the decisions the owner must make first. The guide
remains the reference for every command; the section numbers below point to it.

## 0. What is being deployed, and what it is not

- A **private research demonstration**: the M5 API and the M7 dashboard behind nginx, for named
  accounts. Every number it serves carries its status: the M2 lead over the best baseline is not
  statistically established, the M3 intervals are `FAILED_CALIBRATION`, every M4 parameter is
  `HYPOTHETICAL`, and the forecasts are an archived projection for July 2026.
- **Not** a public service, **not** an operational planning tool, and not live: nothing is
  fetched from NYC Open Data at run time.
- The commit to deploy is the release-candidate commit once it is approved and merged to `main`
  with CI green, including `deploy-e2e` ([RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md)).

## 1. Decisions and information to collect (before touching the server)

| # | Decision or fact | Recommended | Why |
|---|---|---|---|
| 1 | Provider, plan, region, monthly cost, who pays | any provider offering Ubuntu 24.04 LTS on KVM | the package is tested on Ubuntu 24.04 only |
| 2 | vCPUs and CPU model (`x86-64-v2`: SSE4.2, POPCNT) or aarch64 | 2 vCPU | the locked NumPy refuses CPUs without x86-64-v2 (generic `qemu64` vCPUs fail) |
| 3 | Memory and swap | 2 GB (minimum 1 GB **and** 1 GB swap) | the service uses about 0.28-0.31 GiB with two workers, plus nginx, Ubuntu and builds |
| 4 | Disk | 20 GB (minimum 10 GB, at least 4 GB free) | about 320 MB per release, three kept; logs and caches |
| 5 | Is the server dedicated to ATLAS? | yes | local accounts can reach the loopback API; the threat model assumes one administrator |
| 6 | Administrator account with SSH key and sudo; who else gets sudo | one named administrator | |
| 7 | Exposure profile | **`tunnel` first**, then `private` | see section 3 |
| 8 | Domain name and who controls its DNS (private profile) | a subdomain the owner controls | needed for TLS |
| 9 | Certificate method | Let's Encrypt HTTP-01 (port 80 open), or DNS-01 if port 80 must stay closed | DEPLOYMENT.md section 7 |
| 10 | HSTS duration | the default (one year), only once HTTPS works | browsers then refuse plain HTTP |
| 11 | Network allow-list (`ATLAS_DEPLOY_ALLOW_CIDRS`) or a VPN | an allow-list if the viewers' networks are known | enforce the same networks in ufw |
| 12 | IPv6 for clients | off (`ATLAS_DEPLOY_IPV6=false`) unless needed | per-client limits count each IPv6 address |
| 13 | Accounts: who gets one, where passwords are stored | named people only; a password manager | HTTP Basic is a shared-secret scheme without MFA |
| 14 | Alert channel and external uptime check | e-mail or a webhook, plus an external check of `/health/live` | the local monitor cannot report a dead server |
| 15 | Log retention | nginx 14 days, journal 30 days (the package defaults) | nginx logs client addresses |
| 16 | Where the encrypted configuration backup is stored | off the server, owner's storage | |
| 17 | Maintenance window, who watches, rollback authority | a window with the administrator present; a failed verification is rolled back without further approval | |
| 18 | Licence and data terms (repository) | owner decision | not needed to deploy privately, needed before making the repository or the demo public |

## 2. Read-only preflight (changes nothing on the server)

As the administrator, on the candidate server:

```bash
# what the server is
lsb_release -ds; uname -m; nproc; free -h; df -h / /opt /var /tmp
grep -o -w -E 'sse4_2|popcnt|avx2' /proc/cpuinfo | sort -u
systemctl --version | head -1; timedatectl show -p NTPSynchronized
ss -ltnp                                    # what already listens (8000 must be free; 80/443 free or nginx)
sudo ufw status verbose 2>/dev/null || echo "ufw not installed"

# the ATLAS preflight (standard library only; reads, never writes)
git clone https://github.com/arthur-fay/demand-forecasting-model.git ~/atlas
cd ~/atlas && git checkout <approved commit SHA>
python3 -I src/atlas/deploy/preflight.py --resources standard --release-dir .
sudo python3 -I src/atlas/deploy/preflight.py --resources low --release-dir .    # root sees more
python3 -I src/atlas/deploy/preflight.py --resources standard --release-dir . --json > ~/preflight.json
```

The verdict must not be `UNSUITABLE` (any `[FAIL]`). Expected on a fresh server, and fixed by the
bootstrap: missing `python3.12-venv`, `git`, `nginx`. Not fixable by the bootstrap: the CPU
instruction set, memory, disk and a busy server (the load check fails above 0.7 × CPUs). Send the
JSON report to the owner; the warnings must be acknowledged in writing. After the bootstrap, run
the definitive dependency probe as an ordinary user:
`python3 -I src/atlas/deploy/preflight.py --resources standard --release-dir . --probe-dependencies`
(installs the locked runtime into a temporary directory, imports it and solves an LP, then deletes
it).

## 3. Hosting recommendation: private first

1. **`tunnel`** (first deployment): the API and dashboard listen on loopback only; the owner
   reaches them through `ssh -L 8000:127.0.0.1:8000 admin@server`. Nothing is exposed but SSH.
   Simulations in the browser are disabled in this profile (the API needs a key and a browser must
   never hold one); everything else works.
2. **`private`** (once the tunnel deployment has run cleanly for a while): nginx with TLS and HTTP
   Basic accounts on every route, an optional network allow-list, per-client rate limits.
3. **`public-demo`**: **not approved.** It is refused by the tool until a security review is
   recorded, and it would also need DDoS protection and a log-retention decision. Do not use it in
   phase B.

## 4. Installation sequence

Every command below is in [DEPLOYMENT.md](../DEPLOYMENT.md) with its explanation; run them in this
order, as the administrator, in one maintenance window.

1. **Harden the server** (5.1): key-only SSH (keep a working session open while testing), updates,
   `unattended-upgrades`, NTP, a 1 GB swap file on 1 GB servers, ufw (`deny incoming`;
   `limit OpenSSH`; plus 80 and 443 only for the private profile).
2. **Bootstrap** (5.2): `sudo deploy/scripts/bootstrap-host.sh --resources standard` (read the
   plan), then the same with `--apply`. It installs nginx, Python 3.12 venv, git, OpenSSL, a
   hash-pinned uv, the system users and directories, and `/etc/atlas/deploy.env` (tunnel profile).
   Then run the dependency probe (step 2 above).
3. **Build the release** (5.3): `sudo deploy/scripts/build-release.sh <approved 40-character SHA>`.
   It fetches exactly that commit, installs the locked runtime with hash checks, makes the tree
   read-only and runs the release's own checks as the unprivileged user (snapshot integrity, the M4
   reproduction byte for byte, the API contract, the dashboard build). It prints the release id
   (`1.0.0-<12 hex>`).
4. **Secrets and first activation** (5.4, tunnel): `secrets init`, `validate --installed`,
   `activate <release id>`.
5. **Verify** (8): `sudo atlas-deploy verify` and `sudo atlas-deploy status`; through the tunnel,
   open `http://localhost:8000/` and check the six dashboard pages (section 5 below).
6. **Private profile, when approved** (7): set the profile and domain in `deploy.env`, render the
   ACME-only site, obtain the certificate with certbot (deploy hook `systemctl reload nginx`),
   create the accounts (`secrets add-user NAME --generate`), activate the release again, and check
   `curl -su NAME https://<domain>/health/ready`.
7. **Monitoring** (14): confirm `systemctl list-timers atlas-monitor.timer`; install
   `/etc/atlas/alert-hook` for the chosen channel; set up the external uptime check.
8. **Backup** (12): create the first encrypted configuration archive and store it off the server.

## 5. Post-installation verification (acceptance on the server)

All of these must hold before the deployment is called done:

- [ ] `sudo atlas-deploy verify` passes: liveness, readiness (every component), identity (version
  1.0.0, data fingerprint `5ab8b3a5…`, the release directory), workers, scientific integrity
  (`failed_calibration`, `HYPOTHETICAL`, `validated_recommendation: false`), access control (a
  simulation without the key is refused), loopback-only exposure, the proxy socket, the dashboard.
- [ ] `sudo atlas-deploy status` shows the release as current and verified; `history.jsonl` ends
  with `activated`.
- [ ] `systemctl status atlas` reports "ready"; memory well below `MemoryMax`.
- [ ] The dashboard's six pages load (Overview, Forecasting, Uncertainty & risk, Decision
  optimisation, Simulator, Methods & provenance), each with its status labels next to the numbers.
- [ ] Private profile: without an account every URL answers 401; with one, the dashboard loads; a
  simulation from the browser works without the browser holding a key; `curl -I` shows HSTS and the
  dashboard's Content-Security-Policy; an unknown host name gets no certificate; port 80 only
  redirects and serves ACME.
- [ ] The monitor's last report (`journalctl -t atlas-monitor -o cat -n 1`) has no warning or
  critical check; a test alert reaches the chosen channel.
- [ ] `sudo certbot renew --dry-run` succeeds (private profile).
- [ ] A reboot brings everything back (`atlas-deploy verify` after the reboot).

## 6. Operations

- **Daily**: nothing by hand; the monitor runs every minute and alerts on critical checks.
- **Weekly**: read the monitor's warnings; `apt` security updates arrive by `unattended-upgrades`.
  After a `python3.12` update, rebuild the current release (the build re-verifies the M4
  reproduction on the new interpreter).
- **When CI's dependency audit reports a vulnerability**: follow the dependency policy in
  [DEVELOPMENT.md](../DEVELOPMENT.md): fix on a branch, CI green, then build and activate the new
  release.
- **Upgrades** (10): build the new commit, `plan-upgrade`, `activate`, then `prune --keep 3`.
- **Key rotation and account revocation** (12).

## 7. Rollback criteria

Roll back (or let the automatic rollback stand) without further approval when any of these holds
after an activation:

1. `atlas-deploy verify` fails, or the service does not reach "ready" (the activation does this
   automatically and returns to the last verified state);
2. any served response loses its integrity labels, or a number differs from the committed results
   (the activation's scientific checks and `plan-upgrade`'s "research artifacts that differ: none");
3. the dashboard fails to load or shows an API error on any page;
4. memory or restarts trend towards the limits (monitor warnings that persist);
5. a security concern (an unexpected listener, a failed TLS check, an account problem).

How: `sudo atlas-deploy rollback` (the release active before the current one) or
`rollback --to <release id>`; after an interrupted activation, `sudo atlas-deploy recover`. Both
re-render the restored release's nginx site, so the dashboard and the API stay from the same
release (11). Record every rollback and its reason.

## 8. Backup and recovery

Back up only what cannot be rebuilt (12): `/etc/atlas/` (configuration and the simulation key),
`/etc/nginx/atlas/` (accounts), the deployment history and last verified state, optionally
`/etc/letsencrypt/`. Encrypt the archive (`gpg --symmetric`) and keep it off the server. Releases
are rebuilt from the commits in the history. Recovery onto a new server: preflight, harden,
bootstrap, restore the archive, build the last good commit, activate (12).

## 9. What only phase B can verify

The M6 package was tested end to end in a disposable Ubuntu 24.04 systemd container (CI job
`deploy-e2e`, and 22 of 22 in the M8 audit). These remain unverified until the real server:

- the provider's CPU flags, kernel, cgroup configuration and real resource margins on a shared
  1-2 vCPU server with swap;
- DNS, Let's Encrypt issuance and renewal with the deploy hook, HSTS in real browsers;
- ufw on a real network interface, IPv6 on a public network, the provider's own firewall;
- a real reboot, unattended upgrades replacing nginx or Python under a running release;
- `LoadCredential`, `PrivateUsers` and the watchdog under the provider's systemd;
- alert delivery to the chosen channel and the external uptime check;
- performance from the viewers' networks (the measured figures are from local and throttled
  browser runs).
