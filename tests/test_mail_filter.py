from htr.adapters.mail_gmail import is_automated
from htr.adapters.llm_openai_compat import _extract_json


def test_gmail_categories_and_senders_decide():
    assert is_automated("support@devpost.com", "Deadline Extension", {}, [])
    assert is_automated("ntfy@ntfy.sh", "Verify your email for ntfy", {}, [])
    assert is_automated("gemini-notes@google.com", "Notes: Meeting Sep 3", {}, [])
    assert is_automated("someone@shop.example", "Sale!", {}, ["INBOX", "CATEGORY_PROMOTIONS"])
    assert is_automated("someone@shop.example", "hi", {"list-id": "<x.example>"}, [])
    assert not is_automated("marco@shop.example", "Leather bag?", {}, ["INBOX", "CATEGORY_PERSONAL"])
    assert not is_automated("jencortezwalters@gmail.com", "Founders Agreement", {}, ["INBOX"])


def test_json_with_raw_newlines_inside_strings_still_parses():
    content = '''```json
{"body": "Hi,

Thanks for the note.

Tony", "basis": ["the note"]}
```'''
    d = _extract_json(content)
    assert d and d["body"].startswith("Hi,") and d["basis"] == ["the note"]


def test_notification_domains_are_automated():
    assert is_automated("capitalone@notification.capitalone.com", "A payment was made", {}, [])
    assert is_automated("affirm-billing@affirm.com", "You missed your payment", {}, [])
    assert not is_automated("anthony@myersfamily.com", "quote", {}, [])
