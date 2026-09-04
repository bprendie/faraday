#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (( EUID == 0 )); then
  echo 'Run ./install.sh as your desktop user; it uses sudo for the helper.' >&2
  exit 1
fi
for executable in python3 nft nmcli rfkill pkexec systemctl busctl omarchy; do
  command -v "$executable" >/dev/null || { echo "Missing dependency: $executable" >&2; exit 1; }
done
omarchy plugin validate "$project_dir"
plugin_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/io.github.bprendie.faraday"
# Prepare and validate the distributable copy before privileged installation.
python3 "$project_dir/tools/install_plugin.py" "$project_dir" "$plugin_dir"
omarchy plugin validate "$plugin_dir"
sudo /usr/bin/python3 -I "$project_dir/tools/install_helper.py" "$project_dir"
omarchy-shell shell rescanPlugins
python3 - <<'PY'
import json
import subprocess
import time

# Rescanning is asynchronous: wait until the shell has read the new manifest.
for attempt in range(50):
    result = subprocess.run(["omarchy-shell", "shell", "listPlugins"],
                            capture_output=True, text=True, timeout=5)
    try:
        plugins = json.loads(result.stdout)
        if any(plugin.get("id") == "io.github.bprendie.faraday" for plugin in plugins):
            break
    except (ValueError, TypeError):
        pass
    time.sleep(0.2)
else:
    raise SystemExit("Omarchy did not discover Faraday; inspect the shell log before enabling it")
PY
omarchy plugin enable io.github.bprendie.faraday
# A registry rescan can leave cached QML objects alive (including an old timer).
# Finish upgrades with a fresh shell process so the source actually takes effect.
omarchy restart shell
echo 'Faraday installed. It starts in Normal mode. Click the cage to choose a mode.'
