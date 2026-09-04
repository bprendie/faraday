#!/usr/bin/python3
"""Build scalable icon assets from Faraday's original 24px cage geometry."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = '''<circle cx="12" cy="2.6" r="1.4"/>
<path d="M4 21.5V12C4 1.5 20 1.5 20 12V21.5Z M4 18.5H20 M12 4.2C9 6 8.5 8 8.5 10.5 M12 4.2C15 6 15.5 8 15.5 10.5"/>
<path fill="currentColor" stroke="none" d="M7 17.3 9.7 12.5C10.5 9.8 14 10.2 14.6 12L16.5 12.8 14.5 13.5C14.2 16.8 11.6 17.8 9.5 16Z"/>
<path d="M11 16.5 11.5 18.5"/>'''
STATES = {
    "off": '<path d="M4 11 1 10V19L4 20"/>',
    "sealed": '',
    "manual-wifi": '<path stroke-width="1.3" d="M18.9 5Q21 2.4 23.1 5 M20 5.8Q21 4.6 22 5.8"/><circle cx="21" cy="6.9" r=".45" fill="currentColor" stroke="none"/>',
    "error": '<path d="M22.3 9V13"/><circle cx="22.3" cy="15.3" r=".7" fill="currentColor" stroke="none"/>',
    "applying": '<path d="M23 8A2 2 0 1 0 21 10"/>'}
COLORS = {"off": "#b7b8bd", "sealed": "#a9cf86", "manual-wifi": "#eac078", "error": "#ef8b87", "applying": "#eac078"}
LABELS = {"off": "Normal", "sealed": "Caged", "manual-wifi": "Selective", "error": "Error", "applying": "Applying"}

for state, extra in STATES.items():
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" color="{COLORS[state]}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="Faraday {state}">
{BASE}{extra}
</svg>\n'''
    (ROOT / "assets" / f"faraday-{state}.svg").write_text(svg)

cards = "\n".join(f'''<article><img width="144" height="144" src="faraday-{state}.svg" alt="{LABELS[state]}"><h2>{LABELS[state]}</h2><div class="sizes">'''
    + "".join(f'<img width="{size}" height="{size}" src="faraday-{state}.svg" alt="{size}px">' for size in (16, 20, 24, 32)) + '</div></article>' for state in STATES)
(ROOT / "assets" / "preview.html").write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Faraday icon family</title><style>
body{margin:0;padding:64px 32px;background:#191b20;color:#ddd;font:16px system-ui}h1{font-size:36px;letter-spacing:.2em;font-weight:400;margin:0}p{color:#a7a8af;line-height:1.7}main{display:flex;flex-wrap:wrap;gap:24px;margin-top:48px}article{background:#202329;padding:32px;text-align:center;border-radius:16px;flex:1;min-width:150px}h2{font-size:16px;font-weight:400;margin:24px 0}.sizes{display:flex;align-items:center;justify-content:center;gap:20px;height:40px}footer{margin-top:40px;color:#a7a8af}
</style><h1>faraday</h1><p>A bird, a cage, and a deliberate connection.<br>Scalable artwork for the Omarchy bar.</p><main>''' + cards + '</main><footer>Small-size previews: 16 · 20 · 24 · 32 px. State differs by shape as well as color.</footer></html>\n')
