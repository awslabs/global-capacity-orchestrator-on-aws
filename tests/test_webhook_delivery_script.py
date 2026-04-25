"""
Tests for scripts/test_webhook_delivery.py.

The script itself is a manual integration harness — it starts a local HTTP
server, dispatches a webhook through the real ``WebhookDispatcher``, and
prints the result. These unit tests cover the script's own helpers and the
argparse-driven dispatch in ``main()`` without spinning up a real dispatcher
or hitting the network.

Scope:
    - ``WebhookHandler.do_POST``       — captures headers and body into the
                                         module-level ``received_webhooks``
                                         list and returns 200 + JSON body.
    - ``WebhookHandler.log_message``   — silenced so recordings stay clean.
    - ``start_local_server``           — binds on the requested port, runs
                                         in a daemon thread, shuts down cleanly.
    - ``create_mock_job``              — fixture factory returning a MagicMock
                                         shaped like a completed K8s Job.
    - ``main``                         — argparse branch selection between
                                         local-server and external-URL modes;
                                         exit code propagates from the chosen
                                         async runner.

The script was already underscore-named so a direct import works. We rely on
``testpaths = ["tests"]`` in pytest config to stop pytest from collecting the
script itself as a test module.
"""

import asyncio
import json
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Import the module under test.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import test_webhook_delivery as harness  # noqa: E402 - sys.path set above

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Grab an unused loopback port so tests don't collide on CI."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _clear_received_webhooks():
    """The harness stashes received webhooks in a module-level list.

    Reset before and after every test so assertions remain independent of
    test order.
    """
    harness.received_webhooks.clear()
    yield
    harness.received_webhooks.clear()


# ---------------------------------------------------------------------------
# WebhookHandler
# ---------------------------------------------------------------------------


class TestWebhookHandler:
    """The live HTTP handler should capture everything it receives."""

    def test_do_post_records_headers_body_and_responds_200(self):
        port = _free_port()
        server = harness.start_local_server(port)
        try:
            body = json.dumps({"event": "job.completed", "job": "test-1"}).encode()
            req = urllib.request.Request(
                url=f"http://127.0.0.1:{port}/webhook",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-GCO-Event": "job.completed",
                    "X-GCO-Cluster": "gco-us-east-1",
                    "X-GCO-Region": "us-east-1",
                    "X-GCO-Signature": "sha256=deadbeef",
                },
                method="POST",
            )
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback only
                assert resp.status == 200
                assert resp.headers["Content-Type"] == "application/json"
                assert json.loads(resp.read()) == {"status": "received"}
        finally:
            server.shutdown()
            server.server_close()

        # The handler appended a complete record to the module-level buffer.
        assert len(harness.received_webhooks) == 1
        received = harness.received_webhooks[0]
        assert received["path"] == "/webhook"
        # ``dict(self.headers)`` preserves whatever case urllib put on the wire.
        # urllib (via email.message) title-cases custom headers — ``X-GCO-Event``
        # becomes ``X-Gco-Event`` — so assert on that form. The handler's own
        # ``self.headers.get(...)`` call is case-insensitive, so the CLI output
        # prints the original casing regardless.
        assert received["headers"]["X-Gco-Event"] == "job.completed"
        assert received["headers"]["X-Gco-Cluster"] == "gco-us-east-1"
        assert received["headers"]["X-Gco-Region"] == "us-east-1"
        assert received["headers"]["X-Gco-Signature"] == "sha256=deadbeef"
        assert json.loads(received["body"]) == {"event": "job.completed", "job": "test-1"}
        # Timestamp is ISO-8601; parsing it round-trips.
        datetime.fromisoformat(received["timestamp"].replace("Z", "+00:00"))

    def test_do_post_handles_non_json_body(self):
        """Non-JSON bodies still land in received_webhooks verbatim."""
        port = _free_port()
        server = harness.start_local_server(port)
        try:
            req = urllib.request.Request(
                url=f"http://127.0.0.1:{port}/webhook",
                data=b"not json at all",
                headers={"Content-Type": "text/plain"},
                method="POST",
            )
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                assert resp.status == 200
        finally:
            server.shutdown()
            server.server_close()
        assert harness.received_webhooks[0]["body"] == "not json at all"

    def test_log_message_is_silenced(self):
        """
        BaseHTTPRequestHandler.log_message writes to stderr by default; our
        override returns None so the recording output stays clean.
        """
        handler = harness.WebhookHandler.__new__(harness.WebhookHandler)
        assert handler.log_message("%s", "silent") is None


# ---------------------------------------------------------------------------
# start_local_server
# ---------------------------------------------------------------------------


class TestStartLocalServer:
    def test_binds_to_requested_port(self):
        port = _free_port()
        server = harness.start_local_server(port)
        try:
            assert server.server_address[1] == port
        finally:
            server.shutdown()
            server.server_close()

    def test_runs_in_a_daemon_thread(self):
        """
        Daemon-thread means pytest can exit cleanly if a test forgets to
        call server_close() — but every test here does call it, so this
        check mainly pins the implementation choice.
        """
        port = _free_port()
        pre_threads = {t.ident for t in threading.enumerate()}
        server = harness.start_local_server(port)
        try:
            new_threads = [
                t for t in threading.enumerate() if t.ident and t.ident not in pre_threads
            ]
            assert any(
                t.daemon for t in new_threads
            ), "expected start_local_server to spawn at least one daemon thread"
        finally:
            server.shutdown()
            server.server_close()

    def test_server_can_be_reused_after_clean_shutdown(self):
        """
        Port should be released after server_close() so a follow-up test on
        the same port doesn't race on socket reuse. Allow a small delay on
        slower CI hosts where TIME_WAIT takes a tick.
        """
        port = _free_port()
        server = harness.start_local_server(port)
        server.shutdown()
        server.server_close()
        time.sleep(0.05)
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))


# ---------------------------------------------------------------------------
# create_mock_job
# ---------------------------------------------------------------------------


class TestCreateMockJob:
    def test_returns_mock_with_expected_metadata(self):
        job = harness.create_mock_job()
        assert job.metadata.name == "test-webhook-job"
        assert job.metadata.namespace == "gco-jobs"
        assert job.metadata.uid == "test-job-uid-12345"
        assert job.metadata.labels == {"app": "webhook-test", "team": "platform"}

    def test_status_represents_a_completed_job(self):
        job = harness.create_mock_job()
        assert job.status.active == 0
        assert job.status.succeeded == 1
        assert job.status.failed == 0
        assert len(job.status.conditions) == 1
        assert job.status.conditions[0].type == "Complete"
        assert job.status.conditions[0].status == "True"

    def test_completion_time_is_five_minutes_after_start(self):
        """Sanity check on the fixture — the dispatcher computes durations from these."""
        job = harness.create_mock_job()
        delta = job.status.completion_time - job.status.start_time
        assert delta.total_seconds() == 300


# ---------------------------------------------------------------------------
# main() argparse dispatch
# ---------------------------------------------------------------------------


class TestMainDispatch:
    """``main()`` picks between the local-server and external-URL paths."""

    def test_defaults_to_local_server_when_no_url(self):
        mock_local = AsyncMock(return_value=True)
        mock_external = AsyncMock()
        with (
            patch.object(sys, "argv", ["test_webhook_delivery.py"]),
            patch.object(harness, "test_with_local_server", mock_local),
            patch.object(harness, "test_with_external_url", mock_external),
        ):
            exit_code = asyncio.run(harness.main())

        assert exit_code == 0
        mock_local.assert_awaited_once()
        mock_external.assert_not_called()

    def test_uses_external_url_when_provided(self):
        mock_external = AsyncMock(return_value=True)
        mock_local = AsyncMock()
        with (
            patch.object(
                sys,
                "argv",
                ["test_webhook_delivery.py", "--url", "https://example.invalid/webhook"],
            ),
            patch.object(harness, "test_with_external_url", mock_external),
            patch.object(harness, "test_with_local_server", mock_local),
        ):
            exit_code = asyncio.run(harness.main())

        assert exit_code == 0
        mock_external.assert_awaited_once()
        # Positional args: (url, secret). Secret defaults to None.
        assert mock_external.await_args.args == ("https://example.invalid/webhook", None)
        mock_local.assert_not_called()

    def test_passes_secret_to_external_url(self):
        mock_external = AsyncMock(return_value=True)
        with (
            patch.object(
                sys,
                "argv",
                [
                    "test_webhook_delivery.py",
                    "--url",
                    "https://example.invalid/webhook",
                    "--secret",
                    "s3cr3t",
                ],
            ),
            patch.object(harness, "test_with_external_url", mock_external),
        ):
            asyncio.run(harness.main())

        assert mock_external.await_args.args == (
            "https://example.invalid/webhook",
            "s3cr3t",
        )

    def test_returns_nonzero_exit_when_delivery_fails(self):
        mock_local = AsyncMock(return_value=False)
        with (
            patch.object(sys, "argv", ["test_webhook_delivery.py"]),
            patch.object(harness, "test_with_local_server", mock_local),
        ):
            exit_code = asyncio.run(harness.main())
        assert exit_code == 1
