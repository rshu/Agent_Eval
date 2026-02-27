"""
Custom exceptions for the patch evaluation tool.
"""


class PatchEvaluationError(Exception):
    """Base exception for patch evaluation errors."""
    pass


class ConfigurationError(PatchEvaluationError):
    """Raised when there's a configuration error."""
    pass


class FileReadError(PatchEvaluationError):
    """Raised when a file cannot be read."""
    pass


class PromptTemplateError(PatchEvaluationError):
    """Raised when there's an error with the prompt template."""
    pass


class APIError(PatchEvaluationError):
    """Raised when there's an API call error."""
    pass


class ValidationError(PatchEvaluationError):
    """Raised when input validation fails."""
    pass
