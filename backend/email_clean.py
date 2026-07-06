"""Pure text-cleaning helpers for email ingestion.

Everything in this module is side-effect free so it can be unit-tested without
a database or Gmail connection. Heuristics are deliberately simple (spec:
"best-effort heuristics, keep it simple").
"""

import json
import os
import re
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol"}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "img":
            # Tracking pixels and inline images add no text — drop them.
            pass
        elif tag == "a":
            pass

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Convert HTML email body to clean plain text (tracking images dropped)."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed HTML — fall back to a crude tag strip.
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Quoted-reply / signature / disclaimer stripping
# ---------------------------------------------------------------------------

_QUOTE_HEADER_RE = re.compile(
    r"^\s*(On .{0,200}wrote:|Le .{0,200}a écrit :|-{2,}\s*Original Message\s*-{2,}|"
    r"From:\s.+|Sent from my \w+)",
    re.IGNORECASE,
)

_SIGNATURE_MARKERS = re.compile(
    r"^\s*(--\s*$|__+\s*$|Best regards,?|Kind regards,?|Regards,?|Thanks,?$|Thank you,?$|"
    r"Cheers,?$|Sincerely,?|Sent from my \w+)",
    re.IGNORECASE,
)

_DISCLAIMER_RE = re.compile(
    r"(this (e-?mail|message).{0,80}(confidential|intended)|"
    r"if you (are not|received this).{0,80}(recipient|error)|"
    r"unsubscribe|manage (your )?(email )?preferences)",
    re.IGNORECASE,
)


def strip_quoted_replies(text: str) -> str:
    """Remove '> quoted' lines and everything after an 'On ... wrote:' header."""
    out: list[str] = []
    for line in text.splitlines():
        if _QUOTE_HEADER_RE.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def strip_signature(text: str) -> str:
    """Cut the message at a signature marker in the last third of the message."""
    lines = text.splitlines()
    cutoff = max(3, len(lines) * 2 // 3)
    for i in range(len(lines) - 1, -1, -1):
        if i >= cutoff and _SIGNATURE_MARKERS.match(lines[i]):
            lines = lines[:i]
    return "\n".join(lines).strip()


def strip_disclaimers(text: str) -> str:
    """Drop trailing paragraphs that look like legal disclaimers / footer noise."""
    paras = re.split(r"\n\s*\n", text)
    while paras and _DISCLAIMER_RE.search(paras[-1]) and len(paras[-1]) < 800:
        paras.pop()
    return "\n\n".join(paras).strip()


def clean_message_body(body: str, is_html: bool = False) -> str:
    if is_html:
        body = html_to_text(body)
    body = strip_quoted_replies(body)
    body = strip_signature(body)
    body = strip_disclaimers(body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


# ---------------------------------------------------------------------------
# Redaction (Phase 5) — configurable patterns scrubbed before storage
# ---------------------------------------------------------------------------

_DEFAULT_REDACTION_PATTERNS = [
    # Phone numbers (international-ish, 9+ digits with separators)
    r"\+?\d[\d\s().-]{8,}\d",
    # One-time codes: "code is 123456", "OTP: 4821", "verification code 987654"
    r"(?:code|otp|pin)(?:\s+is)?[:\s]+\d{4,8}\b",
]


def load_redaction_patterns() -> list[re.Pattern]:
    """REDACTION_PATTERNS env var (JSON array of regex strings) extends the defaults."""
    patterns = list(_DEFAULT_REDACTION_PATTERNS)
    raw = os.getenv("REDACTION_PATTERNS", "").strip()
    if raw:
        try:
            patterns.extend(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            pass
    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE))
        except re.error:
            pass
    return compiled


def redact(text: str, patterns: list[re.Pattern] | None = None) -> str:
    for pattern in patterns if patterns is not None else load_redaction_patterns():
        text = pattern.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# Addresses / subjects
# ---------------------------------------------------------------------------

_ADDR_RE = re.compile(r'^\s*"?([^"<]*?)"?\s*<([^>]+)>\s*$')


def parse_address(raw: str) -> tuple[str, str]:
    """'Alice Smith <a@x.com>' → ('Alice Smith', 'a@x.com'); bare address → ('', addr)."""
    if not raw:
        return "", ""
    m = _ADDR_RE.match(raw)
    if m:
        return m.group(1).strip(), m.group(2).strip().lower()
    return "", raw.strip().strip("<>").lower()


def split_address_list(raw: str) -> list[tuple[str, str]]:
    """Split a To/Cc header on commas that are not inside quoted names."""
    if not raw:
        return []
    parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', raw)
    return [parse_address(p) for p in parts if p.strip()]


def clean_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and wikilink-hostile characters for use as a title."""
    s = subject or ""
    while True:
        stripped = re.sub(r"^\s*(re|fwd?|aw)\s*:\s*", "", s, flags=re.IGNORECASE)
        if stripped == s:
            break
        s = stripped
    s = s.replace("[", "(").replace("]", ")").strip()
    return s or "Untitled Email"


def is_plus_wiki_address(addr: str, ingest_suffix: str = "+wiki") -> bool:
    """True if addr is a sub-addressed ingestion alias like me+wiki@gmail.com."""
    addr = (addr or "").lower()
    local, _, _domain = addr.partition("@")
    return local.endswith(ingest_suffix.lower())


# ---------------------------------------------------------------------------
# Forwarded-message parsing (Phase 4)
# ---------------------------------------------------------------------------

_FORWARD_MARKER_RE = re.compile(
    r"^\s*-+\s*Forwarded message\s*-+\s*$|^\s*Begin forwarded message:\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_FWD_HEADER_RE = re.compile(r"^(From|Date|Subject|To|Cc):\s*(.+)$", re.IGNORECASE)


def parse_forwarded(text: str) -> dict | None:
    """Split a forwarded email into the forwarder's note and the original content.

    Returns {"note", "headers": {from,date,subject,...}, "body"} or None if the
    text does not look like a forward.
    """
    m = _FORWARD_MARKER_RE.search(text)
    if not m:
        return None
    note = text[: m.start()].strip()
    rest = text[m.end():].lstrip("\n")

    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_headers = True
    for line in rest.splitlines():
        if in_headers:
            hm = _FWD_HEADER_RE.match(line.strip())
            if hm:
                headers[hm.group(1).lower()] = hm.group(2).strip()
                continue
            if not line.strip() and not headers:
                continue
            in_headers = False
        body_lines.append(line)

    return {"note": note, "headers": headers, "body": "\n".join(body_lines).strip()}
