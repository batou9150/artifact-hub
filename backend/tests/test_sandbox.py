import time

import pytest

from artifact_hub import sandbox
from conftest import ALICE, BOB, BASE, auth, create, sandbox_path


def _render(client, who, art_id):
    url = client.get(f"/api/artifacts/{art_id}", headers=auth(who)).json()["render_url"]
    return client.get(sandbox_path(url))


def test_security_headers_on_success(client):
    a = create(client)
    r = _render(client, ALICE, a["id"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    csp = r.headers["content-security-policy"]
    for directive in ("default-src 'none'", "connect-src 'none'", "form-action 'none'", "base-uri 'none'",
                      f"frame-ancestors {BASE}", "sandbox allow-scripts", "img-src data: blob:"):
        assert directive in csp
    assert "allow-same-origin" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert r.headers["x-dns-prefetch-control"] == "off"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "no-store" in r.headers["cache-control"]


def test_security_headers_on_refusal_too(client):
    r = client.get("/a/doesnotexist")
    assert r.status_code == 404
    assert "sandbox allow-scripts" in r.headers["content-security-policy"]


def test_absent_private_and_unauthenticated_look_identical(client):
    a = create(client)
    absent = client.get("/a/doesnotexist")
    no_ticket = client.get(f"/a/{a['id']}")
    wrong_user = client.get(f"/a/{a['id']}", headers=auth(BOB))
    for r in (no_ticket, wrong_user):
        assert (r.status_code, r.text) == (absent.status_code, absent.text) == (404, "Not available")


def test_html_served_verbatim_to_owner(client):
    a = create(client, body="<script>document.title='x'</script><p>ok</p>")
    assert _render(client, ALICE, a["id"]).text == "<script>document.title='x'</script><p>ok</p>"


def test_bearer_header_also_accepted(client):
    a = create(client, body="<p>b</p>")
    assert client.get(f"/a/{a['id']}", headers=auth(ALICE)).text == "<p>b</p>"


def test_current_version_after_update(client):
    a = create(client, kind="text", body="v1")
    client.patch(f"/api/artifacts/{a['id']}", json={"body": "v2"}, headers=auth(ALICE))
    assert "v2" in _render(client, ALICE, a["id"]).text


def test_markdown_neutralizes_active_content(client):
    md = ("# Title\n\n[click](javascript:alert(1)) [ok](https://example.com)\n\n<script>alert(2)</script>\n\n"
          "- item **bold**\n\n```\n<b>code</b>\n```\n")
    a = create(client, kind="markdown", body=md)
    html = _render(client, ALICE, a["id"]).text
    assert "javascript:" not in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<a href="https://example.com" rel="noopener noreferrer">ok</a>' in html
    assert "<h1>Title</h1>" in html and "<strong>bold</strong>" in html
    assert "&lt;b&gt;code&lt;/b&gt;" in html
    assert "click" in html


def test_text_is_escaped(client):
    a = create(client, kind="text", body="<img src=x onerror=alert(1)>")
    html = _render(client, ALICE, a["id"]).text
    assert "<img" not in html and "&lt;img" in html


def test_ticket_is_bound_to_artifact_version_and_expiry():
    s = "k"
    t = sandbox.mint_ticket(s, "A", "u@example.com", None, 60)
    assert sandbox.verify_ticket(s, t, "A", None) == "u@example.com"
    assert sandbox.verify_ticket(s, t, "B", None) is None          # other artifact
    assert sandbox.verify_ticket(s, t, "A", 1) is None             # other version
    assert sandbox.verify_ticket("other", t, "A", None) is None    # other key
    payload, sig = t.split(".")
    assert sandbox.verify_ticket(s, payload + "." + sig[::-1], "A", None) is None
    expired = sandbox.mint_ticket(s, "A", "u@example.com", None, -1)
    assert sandbox.verify_ticket(s, expired, "A", None) is None
    assert sandbox.verify_ticket(s, "garbage", "A", None) is None


def test_ticket_for_other_artifact_refused(client):
    a = create(client, body="<p>a</p>")
    b = create(client, body="<p>b</p>")
    url_a = client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).json()["render_url"]
    ticket = url_a.split("?t=")[1]
    assert client.get(f"/a/{b['id']}?t={ticket}").status_code == 404


def test_ticket_for_current_cannot_open_prior_version(client):
    a = create(client, body="<p>v1</p>")
    client.patch(f"/api/artifacts/{a['id']}", json={"body": "<p>v2</p>"}, headers=auth(ALICE))
    url = client.get(f"/api/artifacts/{a['id']}", headers=auth(ALICE)).json()["render_url"]
    ticket = url.split("?t=")[1]
    assert client.get(f"/a/{a['id']}/v/1?t={ticket}").status_code == 404


@pytest.mark.parametrize("body,warn", [
    ('<script src="https://cdn.example/x.js"></script>', True),
    ('<link rel="stylesheet" href="//cdn.example/x.css">', True),
    ("<script>fetch('https://api.example/data')</script>", True),
    ("<script>const d=[1,2]</script><img src=\"data:image/png;base64,AA\">", False),
])
def test_external_reference_linter(body, warn):
    assert bool(sandbox.external_reference_warnings("html", body)) is warn
