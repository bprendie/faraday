#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (( EUID == 0 )); then
  echo 'Run ./uninstall.sh as your desktop user.' >&2
  exit 1
fi
# Refuses active/pending recovery before disabling the UI or removing any files.
sudo /usr/bin/python3 -I "$project_dir/tools/install_helper.py" --remove
omarchy plugin disable io.github.bprendie.faraday
omarchy restart shell
printf '%s\n' 'Faraday helper removed and widget disabled. Recovery journals retained.'
printf '%s\n' 'The disabled plugin folder is retained; it can now be removed with Omarchy plugin management.'
