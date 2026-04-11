import uuid


def new_request_id() -> str:
    return str(uuid.uuid4())


def redact_token(token: str, keep: int = 4) -> str:
    if not token:
        return ""
    if len(token) <= keep:
        return "*" * len(token)
    return f"{'*' * (len(token) - keep)}{token[-keep:]}"
