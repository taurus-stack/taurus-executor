#!/bin/bash
set -euo pipefail

# Fix ownership of mounted volumes (bind mounts and named volumes may be
# owned by root or a host UID that doesn't match the container's taurus user)
chown -R taurus:taurus /opt/taurus-executor/tls /opt/taurus-executor/data /opt/taurus/versions 2>/dev/null || true

# runuser preserves environment (PYTHONPATH etc.) and works with /sbin/nologin shell
exec runuser -u taurus -- python -m executor_core.main