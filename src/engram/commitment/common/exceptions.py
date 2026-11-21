"""Exceptions shared by the ENGRAM Commitment module."""


class CommitmentError(Exception):
    """Base class for errors raised by Commitment components."""


class EncodingError(CommitmentError):
    """Base class for canonical encoding failures."""


class InvalidTypeError(EncodingError, TypeError):
    """Raised when a value has an unsupported type."""


class ValueOutOfRangeError(EncodingError, ValueError):
    """Raised when a value cannot be represented by its encoding."""


class InvalidEncodingValueError(EncodingError, ValueError):
    """Raised when a value is invalid for its encoding format."""


class ValidationError(CommitmentError):
    """Base class for input invariant violations."""


class InvalidInputError(ValidationError, ValueError):
    """Raised when an input fails a validation requirement."""


class DuplicateValueError(ValidationError, ValueError):
    """Raised when duplicate values are found where uniqueness is required."""
