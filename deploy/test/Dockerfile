# A disposable stand-in for a fresh Ubuntu 24.04 LTS server, for the M6 end-to-end test
# (tests/test_m6_container.py). systemd runs as PID 1; nginx, git and python3.12-venv are
# deliberately absent: the bootstrap script must install them. Never used in production.
ARG BASE=ubuntu:24.04
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      systemd systemd-sysv dbus python3 openssl ca-certificates iproute2 procps curl \
      util-linux sudo \
 && apt-get clean && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty@tty1.service console-getty.service systemd-logind.service \
      systemd-timesyncd.service apt-daily.timer apt-daily-upgrade.timer \
 && mkdir -p /var/log/journal
# Docker mounts are private; systemd hands credentials to services through mount
# propagation, as on a real server. Make the container's mounts shared at every boot.
RUN printf '%s\n' '[Unit]' 'Description=Shared mount propagation (container test stand-in only)' \
      'DefaultDependencies=no' 'Before=sysinit.target' \
      '[Service]' 'Type=oneshot' 'ExecStart=/usr/bin/mount --make-rshared /' \
      '[Install]' 'WantedBy=sysinit.target' > /etc/systemd/system/e2e-shared-mounts.service \
 && systemctl enable e2e-shared-mounts.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
