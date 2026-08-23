from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autonomic_kb.obsidian import ObsidianBridge, parse_help_commands


class ObsidianBridgeTests(unittest.TestCase):
    def test_status_capabilities_and_targeted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary).resolve()

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if command[-1] == "version":
                    return subprocess.CompletedProcess(command, 0, "1.13.7\n", "")
                if command[-1] == "help":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        "Commands:\n  files  List files\n  property:read  Read property\n  links  List links\n",
                        "",
                    )
                if command[-2:] == ["vault", "info=path"]:
                    return subprocess.CompletedProcess(command, 0, f"{vault}\n", "")
                return subprocess.CompletedProcess(command, 0, "fixture\n", "")

            with (
                patch("autonomic_kb.obsidian.shutil.which", return_value="/usr/bin/obsidian"),
                patch("autonomic_kb.obsidian.subprocess.run", side_effect=fake_run) as run,
            ):
                bridge = ObsidianBridge(vault, vault_name="ci-vault")
                status = bridge.status()
                self.assertTrue(status.responsive)
                self.assertTrue(status.vault_responsive)
                self.assertEqual(status.vault_path, str(vault))
                capabilities = bridge.capabilities()
                self.assertTrue({"files", "property:read", "links"}.issubset(capabilities["discovered_commands"]))
                bridge.run("read", "path=Source.md")
                command = run.call_args_list[-1].args[0]
                self.assertEqual(
                    command,
                    ["/usr/bin/obsidian", "vault=ci-vault", "read", "path=Source.md"],
                )

    def test_help_parser_only_returns_top_level_commands(self) -> None:
        help_text = """Commands:
  files                 List files
    folder=<path>       - Filter by folder
    total               - Return file count
  property:read         Read a property
    name=<name>         - Property name

Developer:
  dev:screenshot        Take a screenshot
    path=<filename>     - Output file path
"""
        self.assertEqual(
            parse_help_commands(help_text),
            ["dev:screenshot", "files", "property:read"],
        )

    def test_timeout_is_returned_as_a_completed_process(self) -> None:
        timeout = subprocess.TimeoutExpired(["obsidian", "version"], 1, output=b"partial")
        with (
            patch("autonomic_kb.obsidian.shutil.which", return_value="/usr/bin/obsidian"),
            patch("autonomic_kb.obsidian.subprocess.run", side_effect=timeout),
        ):
            result = ObsidianBridge(Path.cwd()).run("version", target_vault=False, timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "partial")
        self.assertIn("timed out", result.stderr)

    def test_missing_binary_is_explicit(self) -> None:
        with patch("autonomic_kb.obsidian.shutil.which", return_value=None):
            status = ObsidianBridge(Path.cwd()).status()
        self.assertFalse(status.available)
        self.assertIn("not found", status.error)


class ObsidianReleaseDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).parents[1] / "scripts" / "download_obsidian_ci.py"
        spec = importlib.util.spec_from_file_location("download_obsidian_ci", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.module = module

    def test_selects_exact_versioned_asset_and_requires_digest(self) -> None:
        release = {
            "html_url": "https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.13.7",
            "assets": [
                {
                    "name": "Obsidian-1.13.7.AppImage",
                    "browser_download_url": "https://github.com/obsidianmd/obsidian-releases/releases/download/v1.13.7/Obsidian-1.13.7.AppImage",
                    "digest": "sha256:" + "a" * 64,
                    "size": 123,
                },
                {"name": "Obsidian-1.13.7.dmg"},
            ],
        }
        asset = self.module.select_appimage_asset(release, "1.13.7")
        self.assertEqual(asset.name, "Obsidian-1.13.7.AppImage")
        self.assertEqual(asset.digest, "sha256:" + "a" * 64)

        release["assets"][0]["digest"] = None
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            self.module.select_appimage_asset(release, "1.13.7")

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "asset"
            path.write_bytes(b"obsidian-integration")
            expected = hashlib.sha256(b"obsidian-integration").hexdigest()
            self.assertEqual(self.module.sha256_file(path), expected)


if __name__ == "__main__":
    unittest.main()
