"""
Context gate for JournalEntry.post() so only vetted callers (posting services, engine, tests) can post.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_journal_post_allowed: ContextVar[bool] = ContextVar("_journal_post_allowed", default=False)


def is_journal_post_allowed() -> bool:
    return _journal_post_allowed.get()


@contextmanager
def permit_journal_post():
    token = _journal_post_allowed.set(True)
    try:
        yield
    finally:
        _journal_post_allowed.reset(token)
