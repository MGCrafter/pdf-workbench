"""Shared user-facing errors without GUI imports."""


class AnnotationError(ValueError):
    """Invalid user annotation, with a message suitable for the UI."""
