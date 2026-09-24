"""Seed a local hub (DEV AUTH MODE only) with a few demo artifacts.

    python scripts/seed_demo.py [http://localhost:8080]
"""
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"


def h(email):
    return {"Authorization": f"Bearer dev:{email}"}


CHART = """<!doctype html><html><head><meta charset="utf-8"><style>
body{font:14px system-ui;margin:24px;color:#222}h1{font-size:18px}.bar{height:22px;background:#3b4bd8;margin:6px 0;color:#fff;padding-left:6px;border-radius:4px}
#net{margin-top:18px;padding:10px;border-radius:8px;background:#f3f4f8;font-family:monospace;font-size:12px;white-space:pre-line}
</style></head><body><h1>Revenue by region (demo data)</h1><div id="bars"></div>
<div id="net">network probe: running...</div>
<script>
const data=[["North",42],["South",35],["East",28],["West",19]];
document.getElementById('bars').innerHTML=data.map(([k,v])=>`<div class="bar" style="width:${v*8}px">${k} ${v}k</div>`).join('');
const out=[];const log=m=>{out.push(m);document.getElementById('net').textContent=out.join('\\n')};
fetch('https://example.com/exfil').then(()=>log('fetch: NOT blocked')).catch(()=>log('fetch: blocked'));
try{const i=new Image();i.onerror=()=>log('image beacon: blocked');i.onload=()=>log('image beacon: NOT blocked');i.src='https://example.com/p.gif'}catch(e){log('image: blocked')}
try{document.cookie;log('document.cookie: '+JSON.stringify(document.cookie))}catch(e){log('cookie access: blocked ('+e.name+')')}
try{localStorage.getItem('x');log('localStorage: accessible')}catch(e){log('localStorage: blocked ('+e.name+')')}
try{log('parent access: '+window.parent.document.title)}catch(e){log('parent access: blocked ('+e.name+')')}
</script></body></html>"""

with httpx.Client(base_url=BASE, timeout=10) as c:
    for who in ("alice@example.com", "bob@example.com", "carol@example.com", "dave@example.com"):
        c.get("/api/me", headers=h(who)).raise_for_status()
    a1 = c.post("/api/artifacts", headers=h("alice@example.com"), json={
        "title": "Revenue by region, last quarter", "kind": "html", "body": CHART,
        "description": "Bar chart of revenue per region with the sandbox network probe.",
        "source_question": "How much revenue did each region generate last quarter?",
        "tags": ["revenue", "regions"]}).json()["artifact"]
    c.patch(f"/api/artifacts/{a1['id']}", headers=h("alice@example.com"),
            json={"body": CHART.replace("42", "44")})
    c.put(f"/api/artifacts/{a1['id']}/sharing", headers=h("alice@example.com"),
          json={"shared_with": ["bob@example.com", "dave@example.com"]})
    c.post("/api/artifacts", headers=h("alice@example.com"), json={
        "title": "Weekly store traffic notes", "kind": "markdown",
        "body": "# Store traffic\n\n- Footfall up **8%** week over week\n- [Method](https://example.com/method)\n"
                "- [bad link](javascript:alert(1))\n\n> Figures are illustrative.",
        "description": "Commentary on weekly footfall.", "source_question": "Is store traffic growing?"})
    b1 = c.post("/api/artifacts", headers=h("bob@example.com"), json={
        "title": "Stock turnover per category", "kind": "text", "body": "category  turnover\nshoes     4.1\nbags      3.2\n",
        "description": "Inventory rotation by product category.",
        "source_question": "Which categories rotate their stock fastest?"}).json()["artifact"]
    c.put(f"/api/artifacts/{b1['id']}/sharing", headers=h("bob@example.com"),
          json={"shared_with": ["alice@example.com"]})
    c.post("/api/artifacts", headers=h("carol@example.com"), json={
        "title": "KPI definitions", "kind": "markdown", "visibility": "shared",
        "body": "# KPI definitions\n\n1. **Revenue**: net of returns\n2. **Margin**: revenue minus cost of goods\n",
        "description": "Shared glossary of business KPIs.", "tags": ["glossary"]})
    for who in ("bob@example.com", "dave@example.com"):
        c.post(f"/api/artifacts/{a1['id']}/views", headers=h(who))
    print("seeded; open http://localhost:5173 and sign in as Alice")
