from app.services.background_processor import BackgroundProcessor


def test_parse_structured_card_reply_key_value_lines() -> None:
    inputs = [
        {"id": "confirmed_short_description", "label": "Short Description"},
        {"id": "confirmed_Category", "label": "Category"},
    ]

    text = """Short Description: Printer issue in lab\nCategory: Hardware"""

    updates = BackgroundProcessor._parse_structured_card_reply(text, inputs)

    assert updates == {
        "confirmed_short_description": "Printer issue in lab",
        "confirmed_Category": "Hardware",
    }


def test_parse_structured_card_reply_json() -> None:
    inputs = [
        {"id": "confirmed_short_description", "label": "Short Description"},
        {"id": "confirmed_Category", "label": "Category"},
    ]

    text = '{"confirmed_short_description": "New short", "Category": "Network"}'

    updates = BackgroundProcessor._parse_structured_card_reply(text, inputs)

    assert updates == {
        "confirmed_short_description": "New short",
        "confirmed_Category": "Network",
    }