class AdapterError(Exception):
    """Base exception for middleware adapter failures."""


class InvalidSignatureError(AdapterError):
    """Raised when the incoming Zoom webhook signature is missing or invalid."""


class MalformedZoomPayloadError(AdapterError):
    """Raised when the incoming Zoom payload cannot be normalized."""


class CopilotTokenError(AdapterError):
    """Raised when a Direct Line token cannot be retrieved or parsed."""


class DirectLineSendError(AdapterError):
    """Raised when sending a user message to Direct Line fails."""


class DirectLineReceiveTimeoutError(AdapterError):
    """Raised when no Direct Line bot reply is received before timeout."""


class ZoomReplyError(AdapterError):
    """Raised when sending a message to Zoom Chatbot API fails."""


class RepositoryError(AdapterError):
    """Raised when repository operations fail."""
