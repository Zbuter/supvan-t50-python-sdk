class SupvanError(Exception):
    """Base exception for the SDK."""


class DependencyError(SupvanError):
    """An optional runtime dependency is unavailable."""


class ValidationError(SupvanError, ValueError):
    """A print job contains invalid settings or objects."""


class CommunicationError(SupvanError):
    """The printer transport failed or timed out."""


class DeviceError(SupvanError):
    """The printer reported a device or media error."""

