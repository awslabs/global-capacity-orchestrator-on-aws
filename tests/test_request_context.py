"""Request correlation ids (gco/services/request_context.py + the 500 helper).

The manifest API's generic 500s deliberately carry no exception text, so the
correlation id is the only thread from a client-reported failure back to the
logged exception. These tests pin the contract:

* ids are server-generated 32-hex values — never taken from client input;
* ``current_request_id`` binds on first use and stays stable within a
  context, so the log line and the response detail always agree;
* ``bind``/``unbind`` restore the previous context exactly (middleware
  hygiene between requests);
* ``api_shared.internal_server_error`` returns the constant detail plus the
  id, logs the full exception with the same id, and leaks nothing;
* the global exception handler reports the same id in its JSON body.
"""

from __future__ import annotations

import contextvars
import logging
import re
from unittest.mock import MagicMock

import pytest

from gco.services import request_context
from gco.services.api_shared import internal_server_error
from gco.services.request_context import (
    REQUEST_ID_HEADER,
    bind_request_id,
    current_request_id,
    new_request_id,
    unbind_request_id,
)

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _in_fresh_context(func):
    """Run ``func`` in a copied context so bind-on-first-use cannot leak."""
    return contextvars.copy_context().run(func)


class TestRequestIdLifecycle:
    def test_new_ids_are_32_hex_and_unique(self) -> None:
        first, second = new_request_id(), new_request_id()
        assert _HEX32.fullmatch(first)
        assert _HEX32.fullmatch(second)
        assert first != second

    def test_bind_makes_the_id_current_and_unbind_restores(self) -> None:
        def scenario() -> None:
            request_id, token = bind_request_id()
            assert current_request_id() == request_id
            unbind_request_id(token)
            # After reset the context is unbound again: the next read binds
            # a fresh id rather than resurrecting the old one.
            assert current_request_id() != request_id

        _in_fresh_context(scenario)

    def test_current_binds_on_first_use_and_stays_stable(self) -> None:
        def scenario() -> None:
            first = current_request_id()
            assert _HEX32.fullmatch(first)
            assert current_request_id() == first

        _in_fresh_context(scenario)

    def test_contexts_are_isolated(self) -> None:
        ids = {
            contextvars.copy_context().run(current_request_id),
            contextvars.copy_context().run(current_request_id),
        }
        assert len(ids) == 2

    def test_header_name_is_the_conventional_one(self) -> None:
        assert REQUEST_ID_HEADER == "X-Request-ID"


class TestInternalServerErrorHelper:
    def test_detail_carries_the_id_and_never_the_exception(self, caplog) -> None:
        def scenario() -> None:
            with caplog.at_level(logging.ERROR, logger="gco.services.api_shared"):
                error = internal_server_error(
                    "listing jobs", RuntimeError("secret-internal-failure-detail")
                )
            request_id = current_request_id()
            assert error.status_code == 500
            assert error.detail == f"Internal server error (request-id: {request_id})"
            assert "secret-internal-failure-detail" not in error.detail
            # The paired log line carries the SAME id plus the full exception,
            # which is exactly what an operator greps for.
            assert f"Error listing jobs (request-id {request_id})" in caplog.text
            assert "secret-internal-failure-detail" in caplog.text

        _in_fresh_context(scenario)

    def test_uses_the_bound_id_when_middleware_already_ran(self) -> None:
        def scenario() -> None:
            request_id, token = bind_request_id()
            try:
                error = internal_server_error("getting job", RuntimeError("boom"))
            finally:
                unbind_request_id(token)
            assert request_id in error.detail

        _in_fresh_context(scenario)


class TestGlobalExceptionHandlerRequestId:
    @pytest.mark.asyncio
    async def test_body_reports_the_bound_request_id(self) -> None:
        import json

        from fastapi import Request

        from gco.services.manifest_api import global_exception_handler

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.url = "http://test/api/v1/manifests"

        async def scenario() -> None:
            request_id, token = bind_request_id()
            try:
                response = await global_exception_handler(mock_request, RuntimeError("boom"))
            finally:
                unbind_request_id(token)
            body = json.loads(response.body)
            assert response.status_code == 500
            assert body["request_id"] == request_id
            assert body["detail"] == "An unexpected error occurred"

        await scenario()

    def test_module_exports_are_reachable_via_the_package_path(self) -> None:
        # The middleware imports through gco.services.request_context; keep
        # the module attribute surface pinned.
        assert callable(request_context.new_request_id)
        assert callable(request_context.current_request_id)
