#!/usr/bin/env python3
"""Focused offline tests for the Python release builder."""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import stat
import subprocess
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


def workflow_archive_check_command() -> list[str]:
    """Extract the Python command from the GitHub Actions folded run block."""
    lines = (ROOT / ".github" / "workflows" / "native.yml").read_text(
        encoding="utf-8"
    ).splitlines()
    step = next(
        index for index, line in enumerate(lines)
        if line.strip() == "- name: Verify ZIP output"
    )
    run = next(
        index for index in range(step + 1, len(lines))
        if lines[index].lstrip().startswith("run:")
    )
    command_lines = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < 10:
            break
        command_lines.append(line.strip())
    command = " ".join(part for part in command_lines if part)
    arguments = shlex.split(command)
    if len(arguments) < 3 or arguments[:2] != ["python", "-c"]:
        raise AssertionError("Verify ZIP output must run an inline Python command")
    return [sys.executable, *arguments[1:]]


def run_workflow_archive_check(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        workflow_archive_check_command(),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


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
            root_dir = Path(temporary)
            (root_dir / "VERSION").write_text(
                (ROOT / "VERSION").read_text(encoding="utf-8"), encoding="utf-8"
            )
            output = root_dir / "release-ci"
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

            result = run_workflow_archive_check(root_dir)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            external.unlink()
            missing_checksum = run_workflow_archive_check(root_dir)
            self.assertNotEqual(missing_checksum.returncode, 0)
            self.assertIn("AssertionError", missing_checksum.stderr)

    def test_workflow_archive_check_rejects_missing_or_invalid_packages(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        expected_root = f"codex-adaptive-agents-{version}-python/"
        with tempfile.TemporaryDirectory(prefix="release-ci-reject-") as temporary:
            root_dir = Path(temporary)
            (root_dir / "VERSION").write_text(version + "\n", encoding="utf-8")
            output = root_dir / "release-ci"
            output.mkdir()
            (output / "SHA256SUMS").write_text("", encoding="utf-8")

            missing = run_workflow_archive_check(root_dir)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("AssertionError", missing.stderr)

            mismatch_version = "0.0.0" if version != "0.0.0" else "0.0.1"
            mismatch_root = f"codex-adaptive-agents-{mismatch_version}-python/"
            wrong_name = output / f"codex-adaptive-agents-{mismatch_version}-python.zip"
            with zipfile.ZipFile(wrong_name, "w") as package:
                package.writestr(mismatch_root + "codex-adaptive-agents.py", "")
                package.writestr(mismatch_root + "SHA256SUMS", "")
            wrong_version_result = run_workflow_archive_check(root_dir)
            self.assertNotEqual(wrong_version_result.returncode, 0)
            self.assertIn("AssertionError", wrong_version_result.stderr)
            wrong_name.unlink()

            wrong_root = output / f"codex-adaptive-agents-{version}-python.zip"
            with zipfile.ZipFile(wrong_root, "w") as package:
                package.writestr("wrong-root/codex-adaptive-agents.py", "")
                package.writestr("wrong-root/SHA256SUMS", "")
            wrong_contents = run_workflow_archive_check(root_dir)
            self.assertNotEqual(wrong_contents.returncode, 0)
            self.assertIn("AssertionError", wrong_contents.stderr)
            wrong_root.unlink()

            with zipfile.ZipFile(wrong_root, "w") as package:
                package.writestr(expected_root + "codex-adaptive-agents.py", "")
            missing_package_checksum = run_workflow_archive_check(root_dir)
            self.assertNotEqual(missing_package_checksum.returncode, 0)
            self.assertIn("AssertionError", missing_package_checksum.stderr)

            for forbidden_path in ("__pycache__/rogue.pyc", "rogue.pyc"):
                with zipfile.ZipFile(wrong_root, "w") as package:
                    package.writestr(expected_root + "codex-adaptive-agents.py", "")
                    package.writestr(expected_root + "SHA256SUMS", "")
                    package.writestr(expected_root + forbidden_path, "")
                pycache = run_workflow_archive_check(root_dir)
                self.assertNotEqual(pycache.returncode, 0)
                self.assertIn("AssertionError", pycache.stderr)

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
