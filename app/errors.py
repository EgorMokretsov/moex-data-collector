"""Errors that the command line can display without a traceback."""


class CollectorError(Exception):
    """A source, download or extraction operation could not be completed."""
