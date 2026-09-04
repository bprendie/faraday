# Publishing readiness

Reviewed against the [Omarchy publishing guide](https://plugins.omarchy.org/publish.html)
and [development guide](https://plugins.omarchy.org/develop.html) on 2026-09-04.

| Requirement | Faraday |
| --- | --- |
| Root manifest | Present; permanent namespaced ID, schema 1, bar-widget entry point, version and author |
| README and license | Present; MIT also declared in manifest |
| Safe installation/removal | Explicit privileged helper setup; guarded removal; original recovery journals retained; no automatic lockdown on install |
| Plugin files without symlinks | Installer creates a curated copy; preserves previous installation outside plugin discovery |
| Optional preview | Marketplace screenshot in root preview.png; README screenshot in faraday.png; original SVG state icons and HTML preview under assets/ |
| Public GitHub repository | [bprendie/faraday](https://github.com/bprendie/faraday) |
| Marketplace submission | Pending final lifecycle testing and submission |

The marketplace installs the widget checkout. Users must run `./install.sh`
from that checkout to install the privileged helper. The widget reports an
unavailable helper until setup completes. Run `./uninstall.sh` in Normal before
removing the plugin through Omarchy. Marketplace removal alone cannot safely
undo a root-managed active firewall or recovery journal.

Before submission:

1. Run the README verification commands and review `git status` for accidental
   files. Do not commit `output/`, caches, private desktop captures, or machine data.
   The reviewed `faraday.png` widget screenshot is intended for publication.
2. Exercise installation, widget open/close/Escape, disable/re-enable, shell
   restart, and guarded removal on a disposable system. Physical mode switching
   and restoration have been tested on one laptop; this is not broad hardware
   certification. NFC depends on the adapter being exposed through rfkill.
3. Commit the reviewed release and push to a public GitHub repository. Validate
   that exact commit with `omarchy plugin validate` after a fresh clone.
4. Use the publishing guide's submission form with the repository URL, an
   appropriate category, and tags. Automated checks and maintainer approval
   determine listing acceptance.

Faraday is an independent third-party plugin. A marketplace listing does not
constitute an Omarchy security audit or endorsement.
