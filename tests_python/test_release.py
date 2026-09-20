#!/usr/bin/env python3
"""Focused offline tests for the Python release builder."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_python", ROOT / "scripts" / "release_python.py")
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseBuilderTest(unittest.TestCase):
    def test_safe_paths_reject_traversal_and_platform_paths(self):
        for value in ("", ".", "..", "../secret", "a/../b", "/absolute", "C:/absolute",
                      "a\\b", "a\x00b"):
            with self.subTest(value=value):
                with self.assertRaises(release.ReleaseError):
                    release.safe_relative(value)
        self.assertEqual(release.safe_relative(".gitignore"), ".gitignore")
        self.assertEqual(release.safe_pattern("codex_adaptive_agents/**/*.py"), "codex_adaptive_agents/**/*.py")

    def test_checksum_coverage_and_tamper_detection(self):
        with tempfile.TemporaryDirectory(prefix="release-checksum-") as temporary:
            root = Path(temporary)
            (root / "module.py").write_text("print('ok')\n", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")
            release.write_package_checksums(root)
            release.verify_package_checksums(root)
            (root / "module.py").write_text("print('changed')\n", encoding="utf-8")
            with self.assertRaises(release.ReleaseError):
                release.verify_package_checksums(root)

    def test_public_markdown_links_are_checked(self):
        with tempfile.TemporaryDirectory(prefix="release-links-") as temporary:
            root = Path(temporary)
            (root / "guide.md").write_text("[notes](notes.txt#part)\n", encoding="utf-8")
            (root / "notes.txt").write_text("notes\n", encoding="utf-8")
            release.check_public_paths(root)

            bad_link = root / "bad-link"
            bad_link.mkdir()
            (bad_link / "README.md").write_text("[missing](missing.md)\n", encoding="utf-8")
            with self.assertRaises(release.ReleaseError):
                release.check_public_paths(root)

            private = root / "private"
            private.mkdir()
            machine_root = "/" + "Users/example"
            (private / "README.md").write_text(
                "See " + machine_root + "/private.txt\n", encoding="utf-8"
            )
            with self.assertRaises(release.ReleaseError):
                release.check_public_paths(root)

    def test_current_source_build_is_allowlisted_and_validated(self):
        manifest = release.load_manifest()
        with tempfile.TemporaryDirectory(prefix="release-package-") as temporary:
            output = Path(temporary) / "output"
            archive, external = release.build_release(output)
            self.assertTrue(archive.is_file())
            self.assertTrue(external.is_file())
            release.validate_archive(archive, manifest)

            root = release.archive_root(manifest)
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                self.assertIn(root + "/", names)
                self.assertIn(root + "/codex-adaptive-agents.py", names)
                self.assertIn(root + "/scripts/release_python.py", names)
                self.assertIn(root + "/tests_python/test_release.py", names)
                self.assertNotIn(root + "/codex-adaptive-agents", names)
                self.assertFalse(any("/cmd/" in name or "/internal/" in name for name in names))
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
                checksum = package.read(root + "/SHA256SUMS").decode("utf-8")
                self.assertNotIn("SHA256SUMS", checksum)
                entries = [line.split("  ", 1)[1] for line in checksum.splitlines()]
                regular = {
                    name[len(root) + 1 :]
                    for name in names
                    if name.startswith(root + "/")
                    and not name.endswith("/")
                }
                self.assertEqual(set(entries), regular - {"SHA256SUMS"})

            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertIn(digest + "  " + archive.name, external.read_text(encoding="utf-8"))

    def test_existing_archive_is_never_replaced(self):
        manifest = release.load_manifest()
        with tempfile.TemporaryDirectory(prefix="release-overwrite-") as temporary:
            output = Path(temporary) / "output"
            archive, external = release.build_release(output)
            archive_bytes = archive.read_bytes()
            external_bytes = external.read_bytes()
            with self.assertRaisesRegex(release.ReleaseError, "overwrite"):
                release.build_release(output)
            self.assertEqual(archive.read_bytes(), archive_bytes)
            self.assertEqual(external.read_bytes(), external_bytes)
            release.validate_archive(archive, manifest)

    def test_stage_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="release-symlink-") as temporary:
            root = Path(temporary)
            target = root / "target.txt"
            target.write_text("target\n", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this runner")
            with self.assertRaises(release.ReleaseError):
                release.regular_files(root)


if __name__ == "__main__":
    unittest.main()
