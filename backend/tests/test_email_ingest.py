"""Unit tests for the pure email-ingestion logic (no DB / Gmail / LLM needed).

Run from backend/: .venv/bin/python -m pytest tests/ -q
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import email_clean
import email_ingest
import entities
from ingest import IngestAttachment


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(msg_id, from_, to, subject, body, date_ms, extra_headers=None, html=False):
    headers = [
        {"name": "From", "value": from_},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
    ] + (extra_headers or [])
    mime = "text/html" if html else "text/plain"
    return {
        "id": msg_id,
        "internalDate": str(date_ms),
        "payload": {
            "headers": headers,
            "parts": [{"mimeType": mime, "filename": "", "body": {"data": _b64(body)}}],
        },
    }


# ---------------------------------------------------------------------------
# email_clean
# ---------------------------------------------------------------------------

def test_html_to_text_strips_tags_and_tracking():
    html = (
        "<html><head><style>.x{color:red}</style></head><body>"
        "<p>Hello <b>world</b></p><img src='http://track.example/pixel.gif'>"
        "<div>Second line</div></body></html>"
    )
    text = email_clean.html_to_text(html)
    assert "Hello world" in text
    assert "track.example" not in text
    assert "color:red" not in text


def test_strip_quoted_replies():
    text = "Latest reply here.\n\nOn Mon, Jun 1, 2026 at 9:00 AM Bob <bob@x.com> wrote:\n> old stuff\n> more old"
    assert email_clean.strip_quoted_replies(text) == "Latest reply here."


def test_strip_signature():
    text = "Real content line one.\nMore content.\nEven more content here.\nAnd more.\nAnd more again.\n-- \nJohn Doe\nVP of Things"
    out = email_clean.strip_signature(text)
    assert "VP of Things" not in out
    assert "Real content" in out


def test_redaction_phone_and_otp():
    patterns = email_clean.load_redaction_patterns()
    out = email_clean.redact("Call me at +1 (555) 123-4567. Your code is 482913.", patterns)
    assert "555" not in out
    assert "482913" not in out
    assert "[REDACTED]" in out


def test_parse_address_and_list():
    assert email_clean.parse_address('"Smith, Alice" <a@x.com>') == ("Smith, Alice", "a@x.com")
    assert email_clean.parse_address("bob@y.com") == ("", "bob@y.com")
    pairs = email_clean.split_address_list('"Smith, Alice" <a@x.com>, bob@y.com')
    assert pairs == [("Smith, Alice", "a@x.com"), ("", "bob@y.com")]


def test_clean_subject():
    assert email_clean.clean_subject("Re: Re: Fwd: Quarterly [Q3] Plan") == "Quarterly (Q3) Plan"
    assert email_clean.clean_subject("") == "Untitled Email"


def test_is_plus_wiki_address():
    assert email_clean.is_plus_wiki_address("me+wiki@gmail.com")
    assert not email_clean.is_plus_wiki_address("me@gmail.com")


def test_parse_forwarded():
    text = (
        "Interesting article, filing for later.\n\n"
        "---------- Forwarded message ---------\n"
        "From: News Desk <news@example.com>\n"
        "Date: Tue, Jun 2, 2026\n"
        "Subject: The Future of Widgets\n"
        "To: me@gmail.com\n\n"
        "Widgets are changing fast. Here is why.\n"
    )
    fwd = email_clean.parse_forwarded(text)
    assert fwd is not None
    assert fwd["note"] == "Interesting article, filing for later."
    assert fwd["headers"]["subject"] == "The Future of Widgets"
    assert fwd["headers"]["from"] == "News Desk <news@example.com>"
    assert "Widgets are changing fast" in fwd["body"]


def test_parse_forwarded_returns_none_for_plain_mail():
    assert email_clean.parse_forwarded("Just a normal email body.") is None


# ---------------------------------------------------------------------------
# thread → IngestItem mapping
# ---------------------------------------------------------------------------

def _sample_thread():
    return {
        "id": "t123",
        "messages": [
            _message("m1", "Alice Smith <alice@x.com>", "Bob Jones <bob@y.com>",
                     "Project Kickoff", "Kickoff is Monday.\n\nBest regards,\nAlice",
                     1750000000000),
            _message("m2", "Bob Jones <bob@y.com>", "Alice Smith <alice@x.com>",
                     "Re: Project Kickoff",
                     "Works for me.\n\nOn Mon Alice Smith <alice@x.com> wrote:\n> Kickoff is Monday.",
                     1750000600000),
        ],
    }


def test_parse_thread_basics():
    parsed = email_ingest.parse_thread(_sample_thread(), account_email="bob@y.com")
    assert parsed["thread_id"] == "t123"
    assert parsed["subject"] == "Project Kickoff"
    assert ("Alice Smith", "alice@x.com") in parsed["participants"]
    assert parsed["sender"] == "alice@x.com"
    assert not parsed["is_newsletter"]
    assert parsed["gmail_link"].endswith("#all/t123")
    # Quoted reply stripped from Bob's message
    bob = parsed["messages"][1]
    assert "Kickoff is Monday" not in bob["body"]
    assert "Works for me." in bob["body"]


def test_build_ingest_item_maps_contract_fields():
    parsed = email_ingest.parse_thread(_sample_thread(), account_email="bob@y.com")
    item, extra = email_ingest.build_ingest_item(parsed, [])
    assert item.source == "gmail"
    assert item.source_id == "t123"
    assert item.title == "Project Kickoff"
    assert "Alice Smith" in item.body_text and "Works for me." in item.body_text
    assert item.metadata["message_ids"] == ["m1", "m2"]
    assert extra["annotation"] == ""


def test_newsletter_detection():
    thread = {
        "id": "t9",
        "messages": [_message(
            "m9", "Weekly Widget <news@widgets.dev>", "bob@y.com",
            "Widget Weekly #42", "This week in widgets...",
            1750000000000,
            extra_headers=[{"name": "List-Unsubscribe", "value": "<mailto:unsub@widgets.dev>"}],
        )],
    }
    parsed = email_ingest.parse_thread(thread, account_email="bob@y.com")
    assert parsed["is_newsletter"]


def test_forwarded_plus_wiki_thread_uses_original_content():
    body = (
        "Great read!\n\n"
        "---------- Forwarded message ---------\n"
        "From: Author <author@blog.com>\n"
        "Date: Tue, Jun 2, 2026\n"
        "Subject: Original Article Title\n\n"
        "The original article text goes here.\n"
    )
    thread = {
        "id": "t55",
        "messages": [_message(
            "m55", "Bob Jones <bob@y.com>", "Bob Jones <bob+wiki@y.com>",
            "Fwd: Original Article Title", body, 1750000000000,
            extra_headers=[{"name": "Delivered-To", "value": "bob+wiki@y.com"}],
        )],
    }
    parsed = email_ingest.parse_thread(thread, account_email="bob@y.com")
    assert parsed["plus_wiki"]
    item, extra = email_ingest.build_ingest_item(parsed, [])
    assert item.title == "Original Article Title"
    assert extra["annotation"] == "Great read!"
    assert "The original article text goes here." in item.body_text
    assert item.author in ("Author", "author@blog.com")


def test_forwarder_note_is_redacted():
    body = (
        "Call me about this: +1 (555) 123-4567.\n\n"
        "---------- Forwarded message ---------\n"
        "From: Author <author@blog.com>\n"
        "Subject: Original Article Title\n\n"
        "The original article text goes here.\n"
    )
    thread = {
        "id": "t56",
        "messages": [_message(
            "m56", "Bob Jones <bob@y.com>", "Bob Jones <bob+wiki@y.com>",
            "Fwd: Original Article Title", body, 1750000000000,
            extra_headers=[{"name": "Delivered-To", "value": "bob+wiki@y.com"}],
        )],
    }
    parsed = email_ingest.parse_thread(thread, account_email="bob@y.com")
    item, extra = email_ingest.build_ingest_item(parsed, [], email_clean.load_redaction_patterns())
    assert "555" not in extra["annotation"]
    assert "[REDACTED]" in extra["annotation"]
    assert "555" not in item.metadata["annotation"]


def test_attachment_text_extraction_txt_and_unknown():
    assert email_ingest.extract_attachment_text("notes.txt", b"hello world") == "hello world"
    assert email_ingest.extract_attachment_text("evil.exe", b"\x00\x01") == ""


def test_render_email_page_contains_link_participants_and_changelog():
    parsed = email_ingest.parse_thread(_sample_thread(), account_email="bob@y.com")
    item, extra = email_ingest.build_ingest_item(parsed, [])
    page = email_ingest.render_email_page(
        parsed, extra["sections"], "", ["report.pdf"], ["Alice Smith"],
        ["- 2026-07-03: imported thread with 2 message(s) (manual)."],
        {"alice@x.com": "Alice Smith"},
    )
    assert "[open in Gmail](https://mail.google.com/mail/u/0/#all/t123)" in page
    assert "[[Alice Smith]]" in page
    assert "[[report.pdf]]" in page
    assert "## Changelog" in page
    assert "imported thread" in page


def test_changelog_roundtrip():
    parsed = email_ingest.parse_thread(_sample_thread(), account_email="bob@y.com")
    item, extra = email_ingest.build_ingest_item(parsed, [])
    page = email_ingest.render_email_page(
        parsed, extra["sections"], "", [], [], ["- day1: imported."], {}
    )
    lines = email_ingest._changelog_from_article(page)
    assert lines == ["- day1: imported."]


def test_link_mentions_links_first_occurrence_only():
    body = "Acme Corp shipped a thing. Acme Corp is happy."
    out = entities.link_mentions(body, ["Acme Corp"])
    assert out.count("[[Acme Corp]]") == 1


def test_ingest_attachment_model():
    att = IngestAttachment(filename="a.pdf", mime_type="application/pdf", text="hi")
    assert att.filename == "a.pdf"
