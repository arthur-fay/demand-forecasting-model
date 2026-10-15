"""ATLAS deployment tooling (milestone M6, phase A): a portable, verified deployment package.

ATLAS is deployed natively on one Ubuntu LTS server: the M5 API runs as a systemd service
under a dedicated unprivileged user, behind nginx, from an immutable release directory
(``/opt/atlas/releases/<id>``) selected by the ``/opt/atlas/current`` symbolic link. See
``docs/DEPLOYMENT.md`` (the operations guide) and the assets in ``deploy/``.

Every module here uses the Python standard library only, so the tooling runs before any
dependency is installed and never loads the scientific stack:

* :mod:`atlas.deploy.preflight`: read-only assessment of a server (also runnable as a
  standalone script with the system Python);
* :mod:`atlas.deploy.layout`: the filesystem layout of a deployment;
* :mod:`atlas.deploy.siteconfig`: the deployment configuration (``/etc/atlas/deploy.env``),
  validated and refused when unsafe;
* :mod:`atlas.deploy.permissions`: ownership and mode checks of secrets and releases;
* :mod:`atlas.deploy.routes`: the reverse-proxy route allow-list, derived from the
  committed OpenAPI contract;
* :mod:`atlas.deploy.render`: the systemd units, nginx site and runtime environment;
* :mod:`atlas.deploy.manifest`: the release manifest (commit, versions, lock, data and
  artifact fingerprints);
* :mod:`atlas.deploy.verify`: health-based post-deployment verification;
* :mod:`atlas.deploy.releases`: activation, rollback, recovery, pruning and upgrade plans;
* :mod:`atlas.deploy.monitor`: the lightweight periodic health check;
* :mod:`atlas.deploy.cli`: the ``atlas-deploy`` command.

Nothing here contacts or modifies a server by itself: every host change is an explicit
operator command, documented in the operations guide.
"""
