from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.benchmark import BenchmarkRunner
from autonomic_kb.cli import main
from autonomic_kb.config import KBConfig
from autonomic_kb.mcp_server import MCPServer
from tests.support import make_vault, write_memory


class CliMcpBenchmarkTests(unittest.TestCase):
    def test_cli_init_index_status_and_retrieve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "new-vault"
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--json", "init", str(vault)]), 0)
                self.assertEqual(main(["--json", "--vault", str(vault), "index"]), 0)
                self.assertEqual(main(["--json", "--vault", str(vault), "status"]), 0)
                self.assertEqual(main(["--json", "--vault", str(vault), "retrieve", "project knowledge", "--budget", "150"]), 0)

    def test_mcp_initialize_and_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = make_vault(Path(temporary))
            server = MCPServer(config)
            initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
            tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {item["name"] for item in tools["result"]["tools"]}
            self.assertIn("kb_retrieve", names)
            self.assertIn("kb_remember", names)

    def test_benchmark_reports_positive_savings_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = make_vault(root)
            write_memory(
                config, "test.md", "kb:repository:command:test", "Test suite",
                "Run python unittest complete suite", memory_type="command", applies_to=["tests/**"],
            )
            # Add background corpus so the no-KB full-read proxy is meaningful.
            for number in range(6):
                write_memory(
                    config, f"background/{number}.md", f"kb:repository:fact:bg-{number}",
                    f"Background {number}", ("unrelated project context " * 40) + str(number),
                )
            tasks = root / "tasks.json"
            tasks.write_text(json.dumps([{
                "name": "test", "task": "What command runs unittest?", "budget": 140,
                "paths": ["tests/test_cli.py"], "expected_ids": ["kb:repository:command:test"],
            }]), encoding="utf-8")
            result = BenchmarkRunner(config).run(tasks)
            self.assertGreater(result["summary"]["net_savings"], 0)
            self.assertEqual(result["summary"]["mean_expected_coverage"], 1.0)


if __name__ == "__main__":
    unittest.main()
