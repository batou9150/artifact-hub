"""Sandbox content endpoint helpers: the isolating response headers, server-side
rendering of Markdown/text, render tickets, and the external-reference linter.

Isolation model (carried over from the reference implementation):
  * the body is served with `Content-Security-Policy: ... sandbox allow-scripts`
    as a RESPONSE HEADER (the untrusted body cannot strip it). The `sandbox`
    directive without `allow-same-origin` puts the document in an opaque origin
    even on a direct top-level load, so its scripts cannot read this origin's
    storage, cookies or the SPA's tokens, even though it is served under /a/* on
    the app's own host;
  * the viewer embeds it in `<iframe sandbox="allow-scripts">`, never with
    `allow-same-origin` (the pair lets a frame remove its own sandbox);
  * no network egress: default-src/connect-src 'none', form-action 'none',
    images/fonts/media only from data:/blob:, speculative DNS prefetch off;
  * framing pinned to the app origin with frame-ancestors.

Residual risk (documented in docs/security.md): browsers have no enforced CSP
directive for frame self-navigation, so script inside an HTML artifact can still
navigate its own frame to an external URL. Fetch/XHR/WebSocket/beacon/form/img/
script loads and popups are all blocked.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import time


def csp(frame_ancestors: str) -> str:
    return "; ".join([
        "default-src 'none'",
        # Inline and eval'd script is deliberate: artifacts are self-contained
        # documents. Safe only because the origin is opaque and egress is dead.
        "script-src 'unsafe-inline' 'unsafe-eval' blob:",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "font-src data:",
        "media-src data: blob:",
        "connect-src 'none'",
        "form-action 'none'",
        f"frame-ancestors {frame_ancestors}",
        "base-uri 'none'",
        "child-src blob:",
        "worker-src blob:",
        "sandbox allow-scripts",
    ])


def security_headers(frame_ancestors: str) -> dict:
    return {
        "Content-Security-Policy": csp(frame_ancestors),
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-DNS-Prefetch-Control": "off",
        # The iframe URL carries a short-lived render ticket: never leak it.
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, no-store",
    }


# ── Markdown / text rendering ───────────────────────────────────────────────

_SAFE_LINK = re.compile(r"^(https?://|mailto:)", re.I)

_BASE_STYLE = (
    "body{font:15px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:820px;"
    "margin:24px auto;padding:0 20px;color:#1d2030;background:#fff}"
    "pre{background:#f5f6fa;padding:12px;border-radius:8px;overflow:auto}"
    "code{background:#f5f6fa;padding:1px 4px;border-radius:4px}pre code{background:none;padding:0}"
    "blockquote{border-left:3px solid #d6d8e6;margin:0;padding-left:14px;color:#555}"
    "table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:4px 8px}"
    "@media (prefers-color-scheme: dark){body{background:#14161f;color:#e6e7ee}"
    "pre,code{background:#1f2230}blockquote{border-color:#3a3e55;color:#aab}}"
)


def _inline(text: str) -> str:
    """Inline Markdown over ALREADY-ESCAPED text: code, bold, italic, links with
    an allow-listed scheme. A javascript:/data: link keeps its label, loses its href."""
    codes: list[str] = []

    def stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*]+)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", text)  # images: keep alt text only

    def link(m):
        label, url = m.group(1), m.group(2).strip()
        if _SAFE_LINK.match(html.unescape(url)):
            return f'<a href="{url}" rel="noopener noreferrer">{label}</a>'
        return label

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def render_markdown(md: str) -> str:
    """Small, safe Markdown subset: headings, paragraphs, lists, block quotes,
    fenced code, horizontal rules. Everything is HTML-escaped first, so no raw
    HTML or script survives. Not a full CommonMark engine, deliberately."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    list_tag: str | None = None
    i = 0

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    while i < len(lines):
        raw = lines[i]
        if raw.strip().startswith("```"):
            flush_para(); close_list()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(block)) + "</code></pre>")
            i += 1
            continue
        line = html.escape(raw)
        if m := re.match(r"(#{1,6})\s+(.*)$", line):
            flush_para(); close_list()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
        elif re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
            flush_para(); close_list()
            out.append("<hr>")
        elif m := re.match(r"^\s*[-*+]\s+(.*)$", line):
            flush_para()
            if list_tag != "ul":
                close_list(); out.append("<ul>"); list_tag = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
        elif m := re.match(r"^\s*\d+[.)]\s+(.*)$", line):
            flush_para()
            if list_tag != "ol":
                close_list(); out.append("<ol>"); list_tag = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
        elif m := re.match(r"^&gt;\s?(.*)$", line):
            flush_para(); close_list()
            out.append(f"<blockquote>{_inline(m.group(1))}</blockquote>")
        elif line.strip() == "":
            flush_para(); close_list()
        else:
            close_list()
            para.append(line.strip())
        i += 1
    flush_para(); close_list()
    return "\n".join(out)


def document(kind: str, body: str) -> str:
    """HTML artifacts are self-contained documents served verbatim; Markdown and
    text are rendered or escaped into a minimal wrapper."""
    if kind == "html":
        return body
    inner = render_markdown(body) if kind == "markdown" else f"<pre>{html.escape(body)}</pre>"
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<style>{_BASE_STYLE}</style></head><body>{inner}</body></html>")


# ── external references (publish-time warning) ──────────────────────────────

_EXTERNAL = re.compile(
    r"""(?:<script[^>]+src\s*=\s*["']?\s*(?:https?:)?//)"""
    r"""|(?:<link[^>]+href\s*=\s*["']?\s*(?:https?:)?//)"""
    r"""|(?:<img[^>]+src\s*=\s*["']?\s*(?:https?:)?//)"""
    r"""|(?:@import\s+(?:url\()?\s*["']?(?:https?:)?//)"""
    r"""|(?:\bfetch\s*\(\s*["'`]https?://)""",
    re.I,
)


def external_reference_warnings(kind: str, body: str) -> list[str]:
    """HTML that loads scripts, styles, images or data from the network will render
    broken: the sandbox blocks all egress. Warn at publish time instead of letting
    the author discover a blank chart."""
    if kind != "html" or not _EXTERNAL.search(body or ""):
        return []
    return ["This HTML references external resources (CDN scripts, stylesheets, images or fetch calls). "
            "The sandbox blocks all network access: inline every script, style and dataset."]


# ── render tickets ──────────────────────────────────────────────────────────
# An <iframe src> request cannot carry the SPA's bearer token. Instead of a
# session cookie, the API mints a short-lived HMAC ticket bound to (artifact,
# caller, version) after checking access, and the sandbox endpoint verifies it AND
# re-checks access against current state, so a revoked grant stops working even
# with an unexpired ticket.

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_ticket(secret: str, artifact_id: str, email: str, version: int | None, ttl: int) -> str:
    payload = _b64(json.dumps({"a": artifact_id, "e": email, "v": version, "x": int(time.time()) + ttl},
                              separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_ticket(secret: str, ticket: str, artifact_id: str, version: int | None) -> str | None:
    """Return the caller email bound to a valid ticket for exactly this artifact
    and version, else None."""
    try:
        payload, sig = ticket.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
    except Exception:
        return None
    if data.get("a") != artifact_id or data.get("v") != version or int(data.get("x", 0)) < time.time():
        return None
    return data.get("e") or None
