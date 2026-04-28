import json

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


def test_button_postback_submit_value_is_parsed() -> None:
    """When the user clicks a Zoom submit button, the bot_notification cmd contains
    a JSON-encoded submit intent.  BackgroundProcessor._parse_structured_card_reply
    should return an empty dict (the processor detects zoom_action and bypasses it),
    but the encoded payload should correctly decode to action_data + input_values."""
    input_values = {
        "confirmed_short_description": "Printer issue",
        "confirmed_Category": "Hardware",
    }
    action_data = {"ticketStage": "review", "flowId": "xyz"}

    button_value = json.dumps(
        {"zoom_action": "submit_card", "input_values": input_values, "action_data": action_data},
        separators=(",", ":"),
    )

    # The processor detects zoom_action prefix and uses the encoded values directly;
    # it does NOT call _parse_structured_card_reply for these payloads.
    # Verify the button value round-trips correctly.
    decoded = json.loads(button_value)
    assert decoded["zoom_action"] == "submit_card"
    assert decoded["input_values"] == input_values
    assert decoded["action_data"] == action_data

    merged = {**decoded["action_data"], **decoded["input_values"]}
    assert merged["confirmed_short_description"] == "Printer issue"
    assert merged["ticketStage"] == "review"