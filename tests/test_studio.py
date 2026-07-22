from __future__ import annotations

import http.client
import contextlib
import io
import json
import sys
import threading
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHAS_SRC = REPO_ROOT / "chas" / "src"
sys.path.insert(0, str(CHAS_SRC))

from studio import MAX_REQUEST_BYTES, _create_server  # noqa: E402
from studio_service import (  # noqa: E402
    MAX_SOURCE_BYTES,
    StudioLimitError,
    analyze_source,
    run_source,
)


class StudioServiceTests(unittest.TestCase):
    def test_analyze_returns_all_inspector_views(self) -> None:
        result = analyze_source("let answer = 40 + 2\nprint(answer)")

        self.assertTrue(result["ok"])
        self.assertEqual(result["stages"]["lexer"], "passed")
        self.assertEqual(result["stages"]["parser"], "passed")
        self.assertEqual(result["stages"]["types"], "passed")
        self.assertIn("LetDecl answer", result["ast"])
        self.assertTrue(any(token["kind"] == "LET" for token in result["tokens"]))
        if result["bytecode_available"]:
            self.assertEqual(result["stages"]["bytecode"], "passed")
            self.assertIn("ADD", result["bytecode"])

    def test_analyze_keeps_partial_results_for_type_error(self) -> None:
        result = analyze_source("let value: bool = 1")

        self.assertFalse(result["ok"])
        self.assertEqual(result["stages"]["lexer"], "passed")
        self.assertEqual(result["stages"]["parser"], "passed")
        self.assertEqual(result["stages"]["types"], "failed")
        self.assertTrue(result["tokens"])
        self.assertTrue(result["ast"])
        self.assertEqual(result["diagnostics"][0]["category"], "TypeError")

    def test_tree_and_vm_outputs_match(self) -> None:
        source = "for i in 0..4 { print(i * i) }"

        tree = run_source(source, engine="tree")
        vm = run_source(source, engine="vm")

        self.assertTrue(tree["ok"])
        self.assertTrue(vm["ok"])
        self.assertEqual(tree["output"], "0\n1\n4\n9\n")
        self.assertEqual(vm["output"], tree["output"])

    def test_both_engines_bound_infinite_programs(self) -> None:
        source = "let n = 0\nwhile true { n = n + 1 }"

        for engine in ("tree", "vm"):
            with self.subTest(engine=engine):
                result = run_source(source, engine=engine, instruction_limit=100)
                self.assertFalse(result["ok"])
                self.assertEqual(result["stages"]["run"], "failed")
                self.assertIn("instruction limit", result["diagnostics"][0]["message"])

    def test_empty_for_loop_cannot_bypass_tree_budget(self) -> None:
        result = run_source(
            "for i in 0..1000000000 {}",
            engine="tree",
            instruction_limit=25,
        )

        self.assertFalse(result["ok"])
        self.assertIn("instruction limit", result["diagnostics"][0]["message"])

    def test_output_is_bounded(self) -> None:
        result = run_source(
            "for i in 0..100 { print(i) }",
            output_limit=12,
        )

        self.assertFalse(result["ok"])
        self.assertLessEqual(len(result["output"].encode("utf-8")), 12)
        self.assertIn("output limit", result["diagnostics"][0]["message"])

    def test_source_size_is_bounded(self) -> None:
        with self.assertRaises(StudioLimitError):
            analyze_source("x" * (MAX_SOURCE_BYTES + 1))

    def test_deep_nesting_becomes_a_diagnostic(self) -> None:
        source = "(" * 2_000 + "1" + ")" * 2_000
        result = analyze_source(source)

        self.assertFalse(result["ok"])
        self.assertEqual(result["diagnostics"][0]["category"], "LimitError")
        json.dumps(result, allow_nan=False)

    def test_non_finite_float_never_reaches_json(self) -> None:
        result = analyze_source("print(" + "9" * 400 + ".0)")

        self.assertFalse(result["ok"])
        json.dumps(result, allow_nan=False)


class StudioHttpTests(unittest.TestCase):
    TOKEN = "unit-test-token"

    def setUp(self) -> None:
        self.server = _create_server(token=self.TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        token: str | None = TOKEN,
        origin: str | None = None,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object], http.client.HTTPMessage]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = content_type
        if token is not None:
            headers["X-Chas-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        if extra_headers:
            headers.update(extra_headers)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = response.headers
        status = response.status
        connection.close()
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
        return status, decoded, response_headers

    def test_page_is_embedded_and_hardened(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        headers = response.headers
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertIn("CHAS", body)
        self.assertIn(self.TOKEN, body)
        self.assertNotIn("https://", body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_analyze_and_run_endpoints(self) -> None:
        status, analyzed, _ = self.request(
            "POST", "/api/analyze", {"source": "print(42)"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(analyzed["ok"])

        status, executed, _ = self.request(
            "POST", "/api/run", {"source": "print(42)", "engine": "vm"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(executed["ok"])
        self.assertEqual(executed["output"], "42\n")

    def test_post_requires_launch_token(self) -> None:
        status, body, _ = self.request(
            "POST", "/api/analyze", {"source": "print(1)"}, token=None
        )
        self.assertEqual(status, 403)
        self.assertIn("token", body["error"])

        status, _, _ = self.request(
            "POST", "/api/analyze", {"source": "print(1)"}, token="wrong"
        )
        self.assertEqual(status, 403)

    def test_cross_origin_post_is_rejected(self) -> None:
        status, body, _ = self.request(
            "POST",
            "/api/analyze",
            {"source": "print(1)"},
            origin="https://attacker.example",
        )

        self.assertEqual(status, 403)
        self.assertIn("cross-origin", body["error"])

    def test_source_and_request_sizes_are_bounded(self) -> None:
        status, _, _ = self.request(
            "POST", "/api/analyze", {"source": "x" * (MAX_SOURCE_BYTES + 1)}
        )
        self.assertEqual(status, 413)

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        connection.putrequest("POST", "/api/analyze")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("X-Chas-Token", self.TOKEN)
        connection.putheader("Content-Length", str(MAX_REQUEST_BYTES + 1))
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 413)
        connection.close()

    def test_bad_host_and_arbitrary_paths_are_rejected(self) -> None:
        status, _, _ = self.request("GET", "/../../etc/passwd", token=None)
        self.assertEqual(status, 404)

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        connection.putrequest("GET", "/", skip_host=True)
        connection.putheader("Host", "attacker.example")
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 421)
        connection.close()

    def test_server_refuses_non_loopback_binding(self) -> None:
        with self.assertRaises(ValueError):
            _create_server(host="0.0.0.0")

    def test_routine_browser_disconnect_does_not_print_traceback(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            try:
                raise ConnectionResetError("browser closed")
            except ConnectionResetError:
                self.server.handle_error(None, ("127.0.0.1", 1))

        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
