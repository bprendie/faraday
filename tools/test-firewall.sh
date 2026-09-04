#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# No sudo; user and network namespaces isolate every firewall and link mutation.
exec unshare --user --map-root-user --net /usr/bin/python3 "$project_dir/tests/firewall_namespace.py"
