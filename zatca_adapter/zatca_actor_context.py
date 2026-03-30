"""Optional actor (user) for ZATCA status / audit logs during submit pipelines."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_zatca_actor: ContextVar[Any] = ContextVar("_zatca_actor", default=None)


def get_current_zatca_actor():
    return _zatca_actor.get()


@contextmanager
def zatca_actor_scope(actor):
    token = _zatca_actor.set(actor)
    try:
        yield
    finally:
        _zatca_actor.reset(token)
