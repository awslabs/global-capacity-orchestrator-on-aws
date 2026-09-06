"""Per-request correlation ids for the manifest API.

The jobs routes (and the other manifest API routers) return a constant,
generic 500 detail so exception text never reaches a client — which leaves
an operator who receives "I got a 500" with nothing to grep for. This module
gives every request a server-generated correlation id that appears in three
places at once:

* the ``X-Request-ID`` response header (every response, success or error);
* the generic 500 detail (``Internal server error (request-id: <id>)``);
* the paired server-side error log line.

The flow: a client reports the request id from the response, the operator
greps the service logs for it, and lands directly on the logged exception.

Ids are ALWAYS generated server-side (``uuid4().hex``) and never read from
an inbound header: a client-controlled value that ends up next to log lines
would need CWE-117 sanitization and could be replayed across requests to
muddy an investigation. Hex-only ids are log-safe by construction.

The id lives in a :class:`contextvars.ContextVar`, bound by the manifest
API middleware for HTTP traffic. Code that runs outside the middleware
(unit tests calling route coroutines directly) still gets a consistent id:
:func:`current_request_id` binds a fresh one on first use, so the log line
and the response detail produced in the same context always agree.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

#: Response header carrying the correlation id on every manifest API response.
REQUEST_ID_HEADER = "X-Request-ID"

_REQUEST_ID: ContextVar[str | None] = ContextVar("gco_request_id", default=None)


def new_request_id() -> str:
    """A fresh server-generated correlation id (32 lowercase hex characters)."""
    return uuid.uuid4().hex


def bind_request_id() -> tuple[str, Token[str | None]]:
    """Bind a fresh id to the current context; return it with its reset token."""
    request_id = new_request_id()
    return request_id, _REQUEST_ID.set(request_id)


def unbind_request_id(token: Token[str | None]) -> None:
    """Restore the context to its state before the matching :func:`bind_request_id`."""
    _REQUEST_ID.reset(token)


def current_request_id() -> str:
    """The bound correlation id, binding a fresh one on first use.

    Bind-on-first-use keeps the id stable for the remainder of the current
    context, so an error handler that logs the id and then embeds it in the
    response detail reports the same value in both places even when no
    middleware ran (direct route invocation in tests).
    """
    request_id = _REQUEST_ID.get()
    if request_id is None:
        request_id, _ = bind_request_id()
    return request_id
