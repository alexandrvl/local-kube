#!/usr/bin/env bash
# Destroy only the OpenTofu-managed lab nodes (k3s-server and k3s-agent).
# agent-sandbox is not in that state. See docs/infra.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec make destroy
