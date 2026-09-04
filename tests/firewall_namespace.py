"""Real kernel packet tests. Must run inside tools/test-firewall.sh's namespace."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def echo_server(family, port):
    listener = socket.socket(family)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    listener.bind(("::" if family == socket.AF_INET6 else "0.0.0.0", port))
    listener.listen()
    def serve():
        while True:
            sock, _ = listener.accept()
            def echo(client):
                with client:
                    while data := client.recv(1024):
                        client.sendall(data)
            threading.Thread(target=echo, args=(sock,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()


def exchange(sock):
    try:
        sock.sendall(b"faraday")
        return sock.recv(7) == b"faraday"
    except OSError:
        return False


def probe(address):
    try:
        with socket.create_connection((address, 45678), timeout=0.5) as sock:
            return exchange(sock)
    except OSError:
        return False


def peer():
    print(os.getpid(), flush=True)
    stdin = sys.stdin
    established = None
    for line in stdin:
        request = line.strip()
        result = True
        if request == "setup":
            command("ip", "link", "set", "lo", "up")
            command("ip", "addr", "add", "192.0.2.2/24", "dev", "test-peer")
            command("ip", "-6", "addr", "add", "2001:db8::2/64", "dev", "test-peer", "nodad")
            command("ip", "link", "set", "test-peer", "up")
            echo_server(socket.AF_INET, 45678)
            echo_server(socket.AF_INET6, 45678)
        elif request == "ipv4": result = probe("192.0.2.1")
        elif request == "ipv6": result = probe("2001:db8::1")
        elif request == "establish":
            established = socket.create_connection(("192.0.2.1", 45678), timeout=0.5)
            result = exchange(established)
        elif request == "existing": result = exchange(established)
        print(json.dumps(result), flush=True)


def main():
    spec = importlib.util.spec_from_file_location("faraday", Path(__file__).resolve().parents[1] / "backend/faraday.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    child = subprocess.Popen(["unshare", "--net", sys.executable, __file__, "peer"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        pid = child.stdout.readline().strip()
        def ask(value):
            child.stdin.write(value + "\n"); child.stdin.flush()
            return json.loads(child.stdout.readline())
        command("ip", "link", "set", "lo", "up")
        command("ip", "link", "add", "test-wifi", "type", "veth", "peer", "name", "test-peer")
        command("ip", "link", "set", "test-peer", "netns", pid)
        command("ip", "addr", "add", "192.0.2.1/24", "dev", "test-wifi")
        command("ip", "-6", "addr", "add", "2001:db8::1/64", "dev", "test-wifi", "nodad")
        command("ip", "link", "set", "test-wifi", "up")
        ask("setup")
        echo_server(socket.AF_INET, 45678)
        echo_server(socket.AF_INET6, 45678)
        assert ask("ipv4") and ask("ipv6"), "baseline inbound connectivity"
        assert probe("192.0.2.2") and probe("2001:db8::2"), "baseline outbound connectivity"
        assert ask("establish"), "baseline existing inbound session"
        # An unrelated firewall's accept must not bypass Faraday's drop.
        command("nft", "add", "table", "inet", "unrelated")
        command("nft", "add", "chain", "inet", "unrelated", "input", "{ type filter hook input priority 0; policy accept; }")
        system = module.Linux(0)
        system.firewall("manual-wifi", {"device": {"name": "test-wifi"}})
        assert not ask("ipv4"), "manual mode must drop unsolicited IPv4"
        assert not ask("ipv6"), "manual mode must drop unsolicited IPv6"
        assert not ask("existing"), "existing inbound sessions must be blocked"
        assert probe("192.0.2.2"), "manual IPv4 replies must work"
        assert probe("2001:db8::2"), "manual IPv6 replies must work"
        assert probe("127.0.0.1"), "loopback must work"
        # A cellular IP interface gets the same outbound-only policy, and loses
        # egress permission when it is removed from the discovered allowlist.
        command("ip", "link", "set", "test-wifi", "name", "test-cellular")
        assert not probe("192.0.2.2"), "undiscovered cellular interface must be blocked"
        system.firewall("manual-wifi", {"wifi": {"name": "test-wifi"},
                                        "cellular": {"name": "test-cellular"}})
        assert probe("192.0.2.2") and probe("2001:db8::2"), "cellular replies must work"
        assert not ask("ipv4") and not ask("ipv6"), "cellular unsolicited inbound must be blocked"
        system.firewall("manual-wifi", {"wifi": {"name": "test-wifi"}})
        assert not probe("192.0.2.2") and not probe("2001:db8::2"), "removed cellular egress must be blocked"
        system.firewall("sealed", {})
        assert not probe("192.0.2.2"), "sealed outbound IPv4"
        assert not probe("2001:db8::2"), "sealed outbound IPv6"
        assert not ask("ipv4"), "sealed inbound IPv4"
        assert not ask("ipv6"), "sealed inbound IPv6"
        system.remove_firewall()
        assert ask("ipv4") and ask("ipv6"), "restored inbound connectivity"
        assert probe("192.0.2.2") and probe("2001:db8::2"), "restored outbound connectivity"
        command("nft", "list", "table", "inet", "unrelated")
        print("PASS: real IPv4/IPv6 packets, established inbound blocking, reply traffic, loopback, isolation, restoration, unrelated firewall preserved")
    finally:
        child.terminate()
        child.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "peer": peer()
    else: main()
