#!/usr/bin/env bash
# Recreate path is OpenTofu plus Ansible. See docs/infra.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec make apply
