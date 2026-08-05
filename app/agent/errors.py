"""Controlled failures raised by graph execution boundaries."""


class ModelInvocationTimeout(TimeoutError):
    """Raised when one model invocation exceeds its configured deadline."""


class ToolInvocationTimeout(TimeoutError):
    """Raised when one tool execution step exceeds its configured deadline."""
