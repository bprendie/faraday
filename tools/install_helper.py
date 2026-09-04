#!/usr/bin/python3
"""Install/remove root-owned components without discarding recovery state."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FILES = {
    "backend/faraday.py": ("/usr/local/lib/faraday/faraday.py", 0o644),
    "packaging/faraday-helper": ("/usr/local/libexec/faraday-helper", 0o755),
    "packaging/faraday-monitor.service": ("/etc/systemd/system/faraday-monitor.service", 0o644),
    "packaging/faraday-boot.service": ("/etc/systemd/system/faraday-boot.service", 0o644),
    "packaging/io.github.bprendie.faraday.policy": ("/usr/share/polkit-1/actions/io.github.bprendie.faraday.policy", 0o644),
}
UNITS = ["faraday-monitor.service", "faraday-boot.service"]


def require_normal(state, run=subprocess.run):
    if (state / "snapshot.json").exists():
        raise RuntimeError("Restore Faraday to Normal successfully before upgrading or uninstalling")
    result = run(["nft", "-j", "list", "tables"], check=True,
                 capture_output=True, text=True)
    for entry in json.loads(result.stdout)["nftables"]:
        table = entry.get("table", {})
        if table.get("name") == "faraday" and table.get("family") in ("inet", "bridge"):
            raise RuntimeError("Faraday firewall tables remain; recover them before installation/removal")


def safe_target(target):
    for path in (target, *target.parents):
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink destination: {path}")
        if path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
            raise RuntimeError(f"Destination must be root-owned and not group/world writable: {path}")


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Run through install.sh or uninstall.sh")
    remove = sys.argv[1:] == ["--remove"]
    source = None if remove else Path(sys.argv[1]).resolve()
    state = Path("/var/lib/faraday")
    safe_target(state)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    lock = state / "controller.lock"
    safe_target(lock)
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Faraday is busy; wait for the operation to finish and retry")
        require_normal(state)
        for destination, _ in FILES.values():
            safe_target(Path(destination))
        if remove:
            subprocess.run(["systemctl", "disable", "--now", *UNITS], check=True)
            for destination, _ in FILES.values():
                Path(destination).unlink(missing_ok=True)
            # Retain the lock inode and private completed journal for audit/recovery.
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            return
        for relative, (destination, mode) in FILES.items():
            if not (source / relative).is_file() or (source / relative).is_symlink():
                raise RuntimeError(f"Missing or linked installation source: {relative}")
        for relative, (destination, mode) in FILES.items():
            target = Path(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as output, (source / relative).open("rb") as incoming:
                    shutil.copyfileobj(incoming, output)
                    output.flush()
                    os.fsync(output.fileno())
                    os.fchown(output.fileno(), 0, 0)
                    os.fchmod(output.fileno(), mode)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "faraday-boot.service"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "faraday-monitor.service"], check=True)
        subprocess.run(["systemctl", "restart", "faraday-monitor.service"], check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
