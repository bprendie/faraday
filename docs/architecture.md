# Faraday architecture and limits

## Components and authority

`Panel.qml` runs unprivileged inside Omarchy. It renders the cage, reads a
sanitized status file through the installed helper, and requests a fixed mode
through pkexec. It never sends arbitrary shell commands to the privileged
controller. Mode changes require administrator authentication. The initiating
UID is taken from pkexec/sudo, not a user-supplied JSON field.

The root-owned launcher starts Python with `-I` and an absolute module path.
Subprocesses use argument arrays, a fixed PATH and C locale, and timeouts.
Legacy snapshot recovery runs user config operations after dropping all supplementary
groups and changing to the session owner's UID/GID. Root never follows a user
config symlink while retaining root file permissions.

The root-private snapshot is machine-wide and has one owning desktop UID.
Other users cannot silently replace it. `flock` serializes mode changes and
monitor ticks. Atomic writes fsync both file and directory before mutations.

The monitor polls approximately every two seconds, plus command execution
time. It captures newly discovered devices/profiles before changing them,
reapplies drifted controls, and publishes verified health. If it stalls, the
widget marks status stale after 15 seconds. Active controls survive widget
removal; disabling the plugin is not the same operation as turning Faraday Normal.

Apply/restore operations publish `working`, `operation`, `progress`, and
`started` fields. A separate heartbeat thread refreshes status every two seconds
while the main thread waits on radio or connection commands with the controller
lock held. Status writes are serialized and atomic. A live operation renders
as an animated spinner with its current step and elapsed time, even after a
widget reload. Stale status or a completed failed operation still reports a
problem. Previous errors are cleared at the start of a new attempt.

Bluetooth uses two controls: rfkill blocks the radio and BlueZ Adapter1.Powered
switches off the adapter. Original power values are saved by Bluetooth address,
so hci index changes do not apply the wrong adapter's settings. Both active
modes verify that all discovered adapters are powered off. Restoration first
restores rfkill state, then the original Bluetooth power values. D-Bus probes
check service ownership and use `--auto-start=no`; discovery must never start a
radio daemon as a side effect.

## Firewall

Faraday adds early input/output/forward chains in its own `inet` table and a
forward chain in its own `bridge` table, with DROP policies. It does not flush
or restore a whole ruleset. This preserves concurrent changes made by other
firewall tools and avoids replaying stale Docker rules.

An nftables accept in one base chain does not override a drop in another.
See the [nftables verdict documentation](https://netfilter.org/projects/nftables/manpage.html)
and [base-chain priorities](https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains).

Selective allows only the discovered Wi-Fi interfaces for external egress.
Incoming exceptions are reply-direction established/related traffic, DHCPv4,
link-local DHCPv6, and IPv6 router/neighbor messages with hop limit 255. Existing
inbound sessions do not qualify as reply-direction traffic. IPv4 and IPv6
listening ports remain closed to unsolicited connections; loopback works.

“Stealth” means dropping unsolicited IP connection attempts instead of
rejecting them. It does **not** mean invisibility: Wi-Fi management traffic,
ARP, DHCP, neighbor discovery, and deliberate outgoing traffic still identify
a connected host. Application multicast/broadcast egress on Wi-Fi is not
suppressed in this version. This is not a physical Faraday cage.

Ethernet traffic, container forwarding, and VPN tunnel-interface egress are
blocked in both active modes. VPN-aware routing requires an explicit future
mode/policy. Network namespaces, firmware paths, external radios, hardware
offload, and raw packet sockets can fall outside these host IP hooks.
Detected nftables flowtables are refused; this is not a defense against a
privileged local process rewriting networking or using raw sockets.

## Deliberate Wi-Fi

NetworkManager has both per-profile `connection.autoconnect` and per-device
`autoconnect` controls. Faraday journals and disables both. Turning Wi-Fi on is
not treated as permission to join an existing network. Every mode transition
persists a disconnect intent before applying controls, so crash recovery cannot
skip that step. Manual selection uses Omarchy's existing network UI and secret
agent rather than storing or passing credentials through Faraday.

See [nmcli device and connection operations](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html).
IWD can independently initiate automatic connections when
`wifi.iwd.autoconnect` is enabled; see [NetworkManager configuration](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager.conf.html).
The current implementation refuses Selective while the IWD service runs.

The polling monitor is not a transaction across NetworkManager, kernel rfkill,
and the filesystem. A hotplugged adapter or another application's configuration
change can precede the next reconciliation. NetworkManager can preserve active
connections over a service restart; this version cannot assert a fresh human
decision for every re-association performed internally by a driver or manager.
Crash/reboot safety and ordinary saved-network autoconnect are addressed, but
strict event-level association authorization needs a dedicated D-Bus policy
backend. Do not describe polling as an instantaneous enforcement guarantee.

## Desktop settings

Faraday does not manage screen locking, stay-awake, or idle inhibition. There
is no extra idle monitor in the widget and new snapshots contain no idle state.
The legacy recovery worker remains only to restore idle settings recorded by
0.1.0/0.1.1 snapshots. It no longer has an operation to apply an idle override.

## Restoration contract

The original values belong to the first activation. Switching modes never
replaces them. New devices and Wi-Fi profiles get their own first-seen values
journaled before modification. Restore changes only owned fields and tables,
attempts every independent restoration step, and retains failed steps for retry.
Readback verifies settings before completion. A failed reconnect can leave
the original networking restored with a pending recovery journal; the UI shows
the incomplete state and the failing operation.

Reboot cannot preserve live application sessions or hardware switch positions.
USB radio relocation changes its bus identity and requires reconnecting it at
its original location for automatic restore. Deleting a saved network cannot
be undone without its profile/credentials. Baseline configuration is restored;
environmental availability is not fabricated.

Before declaring a stable release, test full apply/restore across machines
with different Wi-Fi, Bluetooth, WWAN and NFC combinations, external adapters,
hardware kill switches, custom lock timings and stay-awake state, Docker/UFW,
firewalld/nftables reloads, NetworkManager restarts, suspend/resume and reboot.
