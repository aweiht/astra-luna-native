#!/usr/bin/env python3
"""Build and verify the public 0.4.0 Python source/runtime ZIP.

The builder deliberately works from an explicit manifest. It never walks the
repository as an implicit release allowlist, follows links, or replaces an
existing archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "VERSION"
MANIFEST_FILE = PROJECT_ROOT / "release-manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
EXPECTED_VERSION = "0.4.0"
PRIVATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|Volumes|private/var|home)/[^\s<>)\"']+"
)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)")
CHECKSUM_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})  (.+)$")


class ReleaseError(RuntimeError):
    """A release input or output failed an auditable package check."""


def _duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ReleaseError(f"non-finite JSON number: {value}")


def read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_key,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read strict JSON {path}: {exc}") from exc


def safe_relative(value: str) -> str:
    """Return a normalized relative POSIX path or reject it."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReleaseError(f"invalid relative path: {value!r}")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ReleaseError(f"non-portable or absolute path: {value!r}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ReleaseError(f"path traversal or empty component: {value!r}")
    return "/".join(parts)


def safe_pattern(value: str) -> str:
    """Validate a manifest glob without expanding or normalizing its glob."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReleaseError(f"invalid manifest glob: {value!r}")
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ReleaseError(f"non-portable or absolute glob: {value!r}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ReleaseError(f"unsafe manifest glob: {value!r}")
    return value


def within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def has_forbidden_component(relative: str) -> bool:
    parts = relative.split("/")
    return any(
        part == "__pycache__"
        or part.endswith(".pyc")
        or (part.startswith(".") and part not in (".gitignore", ".github"))
        for part in parts
    )


def _source_lstat(path: Path, relative: str) -> os.stat_result:
    if not within(path, PROJECT_ROOT):
        raise ReleaseError(f"source path escapes repository: {relative}")
    current = PROJECT_ROOT
    for component in Path(relative).parts:
        current = current / component
        try:
            info = current.lstat()
        except OSError as exc:
            raise ReleaseError(f"missing source path {relative}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ReleaseError(f"symlink is not allowed in source payload: {relative}")
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"cannot stat source path {relative}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseError(f"source path is not a regular file: {relative}")
    return info


def _excluded(relative: str, excluded_paths: Iterable[str]) -> bool:
    normalized = relative.rstrip("/")
    for raw in excluded_paths:
        if not isinstance(raw, str) or not raw:
            raise ReleaseError(f"invalid excluded path: {raw!r}")
        candidate = raw.replace("\\", "/")
        if candidate.endswith("/"):
            if normalized == candidate[:-1] or normalized.startswith(candidate):
                return True
        elif normalized == candidate:
            return True
    return False


def load_manifest() -> dict[str, Any]:
    manifest = read_json(MANIFEST_FILE)
    if not isinstance(manifest, dict):
        raise ReleaseError("release manifest must be an object")
    required = {
        "schema_version",
        "project",
        "version",
        "release",
        "license",
        "archive_format",
        "archive_root_pattern",
        "output_directory",
        "source_equals_runtime",
        "checksums",
        "public_files",
        "source",
        "runtime",
        "excluded_paths",
        "verification",
        "support",
    }
    missing = sorted(required.difference(manifest))
    if missing:
        raise ReleaseError(f"manifest is missing required keys: {', '.join(missing)}")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ReleaseError(f"unsupported manifest schema: {manifest['schema_version']!r}")
    if manifest["project"] != "astra-luna-native":
        raise ReleaseError("manifest project is not astra-luna-native")
    if manifest["version"] != EXPECTED_VERSION:
        raise ReleaseError(f"manifest version must be {EXPECTED_VERSION}")
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReleaseError(f"cannot read VERSION: {exc}") from exc
    if version != EXPECTED_VERSION or manifest["version"] != version:
        raise ReleaseError("VERSION and release manifest disagree")
    if manifest["release"] != "python-source":
        raise ReleaseError("manifest release must be python-source")
    if manifest["license"] != "Apache-2.0":
        raise ReleaseError("manifest must declare Apache-2.0")
    if manifest["archive_format"] != "zip" or manifest["source_equals_runtime"] is not True:
        raise ReleaseError("manifest must describe a source-equals-runtime ZIP")
    if manifest.get("entrypoint") != "astra-luna.py":
        raise ReleaseError("manifest entrypoint must be astra-luna.py")
    if not isinstance(manifest["archive_root_pattern"], str):
        raise ReleaseError("archive_root_pattern must be a string")
    if "{version}" not in manifest["archive_root_pattern"] or "{target}" not in manifest["archive_root_pattern"]:
        raise ReleaseError("archive_root_pattern must contain {version} and {target}")
    source = manifest["source"]
    if not isinstance(source, dict):
        raise ReleaseError("manifest source must be an object")
    required_files = source.get("required_files")
    required_globs = source.get("required_globs")
    if not isinstance(required_files, list) or not required_files:
        raise ReleaseError("source.required_files must be a non-empty list")
    if not isinstance(required_globs, list) or not required_globs:
        raise ReleaseError("source.required_globs must be a non-empty list")
    for value in required_files:
        safe_relative(value)
    for value in required_globs:
        safe_pattern(value)
    public_files = manifest["public_files"]
    if not isinstance(public_files, list) or not public_files:
        raise ReleaseError("public_files must be a non-empty list")
    for value in public_files:
        safe_relative(value)
    excluded_paths = manifest["excluded_paths"]
    if not isinstance(excluded_paths, list):
        raise ReleaseError("excluded_paths must be a list")
    for value in excluded_paths:
        if not isinstance(value, str) or not value:
            raise ReleaseError(f"invalid excluded path: {value!r}")
    runtime = manifest["runtime"]
    if (not isinstance(runtime, dict) or runtime.get("same_as") != "source"
            or runtime.get("entrypoint") != "astra-luna.py"):
        raise ReleaseError("runtime must explicitly equal source")
    checksums = manifest["checksums"]
    if not isinstance(checksums, dict) or checksums.get("package_file") != CHECKSUMS_NAME:
        raise ReleaseError("manifest checksum contract is incomplete")
    return manifest


def collect_source(manifest: dict[str, Any]) -> dict[str, Path]:
    source = manifest["source"]
    excluded_paths = manifest["excluded_paths"]
    collected: dict[str, Path] = {}

    def add(relative: str, path: Path) -> None:
        relative = safe_relative(relative)
        if _excluded(relative, excluded_paths):
            raise ReleaseError(f"manifest required path is excluded: {relative}")
        if has_forbidden_component(relative):
            raise ReleaseError(f"manifest path contains forbidden component: {relative}")
        _source_lstat(path, relative)
        previous = collected.get(relative)
        if previous is not None and previous != path:
            raise ReleaseError(f"duplicate source path: {relative}")
        collected[relative] = path

    for raw in source["required_files"]:
        relative = safe_relative(raw)
        add(relative, PROJECT_ROOT / Path(*relative.split("/")))

    for raw_pattern in source["required_globs"]:
        pattern = safe_pattern(raw_pattern)
        matches = sorted(PROJECT_ROOT.glob(pattern), key=lambda item: item.as_posix())
        if not matches:
            raise ReleaseError(f"required glob matched nothing: {pattern}")
        for match in matches:
            try:
                relative = match.relative_to(PROJECT_ROOT).as_posix()
            except ValueError as exc:
                raise ReleaseError(f"glob escaped repository: {pattern}") from exc
            if match.is_symlink():
                raise ReleaseError(f"symlink is not allowed in glob: {relative}")
            if match.is_dir():
                for child in sorted(match.rglob("*"), key=lambda item: item.as_posix()):
                    child_relative = child.relative_to(PROJECT_ROOT).as_posix()
                    if child.is_symlink():
                        raise ReleaseError(f"symlink is not allowed in glob: {child_relative}")
                    if child.is_dir():
                        if has_forbidden_component(child_relative):
                            raise ReleaseError(f"forbidden directory in glob: {child_relative}")
                        continue
                    add(child_relative, child)
            else:
                add(relative, match)
    return dict(sorted(collected.items()))


def _copy_regular(source: Path, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    os.chmod(target, stat.S_IMODE(mode))


def copy_to_stage(files: dict[str, Path], stage_root: Path) -> None:
    for relative, source in files.items():
        target = stage_root / Path(*relative.split("/"))
        _copy_regular(source, target, source.lstat().st_mode)


def regular_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ReleaseError(f"symlink found in package stage: {path}")
        if path.is_file():
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ReleaseError(f"non-regular package member: {path}")
            result.append(path)
        elif not path.is_dir():
            raise ReleaseError(f"non-regular package member: {path}")
    return result


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def relative_files(root: Path, *, include_checksums: bool = False) -> list[str]:
    names = []
    for path in regular_files(root):
        name = path.relative_to(root).as_posix()
        if include_checksums or name != CHECKSUMS_NAME:
            names.append(name)
    return names


def write_package_checksums(stage_root: Path) -> Path:
    checksum_path = stage_root / CHECKSUMS_NAME
    if checksum_path.exists() or checksum_path.is_symlink():
        raise ReleaseError("package stage already contains SHA256SUMS")
    lines = [
        f"{digest(stage_root / Path(*name.split('/')))}  {name}"
        for name in relative_files(stage_root)
    ]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(checksum_path, 0o644)
    verify_package_checksums(stage_root)
    return checksum_path


def _parse_checksums(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseError(f"cannot read checksum file {path}: {exc}") from exc
    if not lines:
        raise ReleaseError(f"empty checksum file: {path}")
    result: dict[str, str] = {}
    for line in lines:
        match = CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ReleaseError(f"malformed checksum line in {path}: {line!r}")
        checksum, raw_name = match.groups()
        name = safe_relative(raw_name)
        if name == CHECKSUMS_NAME or name in result:
            raise ReleaseError(f"duplicate or self-referencing checksum entry: {name}")
        result[name] = checksum.lower()
    return result


def verify_package_checksums(stage_root: Path) -> None:
    checksum_path = stage_root / CHECKSUMS_NAME
    if checksum_path.is_symlink() or not checksum_path.is_file():
        raise ReleaseError("package SHA256SUMS is missing or not regular")
    entries = _parse_checksums(checksum_path)
    actual = set(relative_files(stage_root))
    if set(entries) != actual:
        missing = sorted(actual.difference(entries))
        extra = sorted(set(entries).difference(actual))
        raise ReleaseError(f"package checksum coverage mismatch; missing={missing}, extra={extra}")
    for name, expected in entries.items():
        actual_digest = digest(stage_root / Path(*name.split("/")))
        if actual_digest != expected:
            raise ReleaseError(f"package checksum mismatch: {name}")


def _public_document_files(root: Path) -> list[Path]:
    # Source and test code can contain checker fixtures or platform examples.
    # Public Markdown is the reader-facing surface whose links and paths must
    # be safe and resolvable.
    return sorted(root.rglob("*.md"), key=lambda item: item.as_posix())


def _check_markdown_links(path: Path, root: Path, text: str) -> None:
    for raw_target in MARKDOWN_LINK_RE.findall(text):
        target = raw_target.strip()
        if target.startswith("<"):
            end = target.find(">")
            if end < 0:
                raise ReleaseError(f"malformed Markdown link in {path}")
            target = target[1:end].strip()
        else:
            target = target.split(None, 1)[0]
        if not target or target.startswith(("#", "http://", "https://", "mailto:", "data:")):
            continue
        if target.startswith("file:") or target.startswith(("/", "\\")):
            raise ReleaseError(f"private or absolute Markdown link in {path}: {target}")
        if any(marker in target for marker in (".orchestrator-dev", ".reference", ".astra-luna")):
            raise ReleaseError(f"private Markdown link in {path}: {target}")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        relative_target = unquote(parsed.path)
        if not relative_target:
            continue
        candidate = (path.parent / Path(*relative_target.split("/"))).resolve(strict=False)
        if not within(candidate, root) or not candidate.is_file() or candidate.is_symlink():
            raise ReleaseError(f"broken or escaping Markdown link in {path}: {target}")


def check_public_paths(root: Path) -> None:
    for path in _public_document_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReleaseError(f"public Markdown is not UTF-8: {path}: {exc}") from exc
        match = PRIVATE_PATH_RE.search(text)
        if match:
            raise ReleaseError(f"private machine path in public Markdown {path}: {match.group(0)}")
        _check_markdown_links(path, root, text)


def _zip_info(name: str, mode: int, *, directory: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    file_mode = stat.S_IFDIR | 0o755 if directory else stat.S_IFREG | (stat.S_IMODE(mode) or 0o644)
    info.external_attr = file_mode << 16
    return info


def archive_root(manifest: dict[str, Any]) -> str:
    root = manifest["archive_root_pattern"].format(version=manifest["version"], target="python")
    return safe_relative(root)


def create_archive(stage_root: Path, archive_path: Path, manifest: dict[str, Any]) -> None:
    if archive_path.exists() or archive_path.is_symlink():
        raise ReleaseError(f"refusing to overwrite existing archive: {archive_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    root = archive_root(manifest)
    files = relative_files(stage_root, include_checksums=True)
    with zipfile.ZipFile(
        archive_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        output.writestr(_zip_info(root + "/", 0o755, directory=True), b"")
        for relative in files:
            source = stage_root / Path(*relative.split("/"))
            output.writestr(
                _zip_info(root + "/" + relative, source.lstat().st_mode),
                source.read_bytes(),
            )


def _member_relative(name: str, root: str) -> str | None:
    if name == root + "/":
        return None
    prefix = root + "/"
    if not name.startswith(prefix):
        raise ReleaseError(f"archive member is outside package root: {name!r}")
    return safe_relative(name[len(prefix) :])


def validate_archive(archive_path: Path, manifest: dict[str, Any]) -> None:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ReleaseError(f"archive is missing or not regular: {archive_path}")
    root = archive_root(manifest)
    with tempfile.TemporaryDirectory(prefix=".verify-python-release-") as temporary:
        extracted = Path(temporary) / root
        seen: set[str] = set()
        root_seen = False
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                if "\x00" in info.filename or "\\" in info.filename:
                    raise ReleaseError(f"unsafe archive member: {info.filename!r}")
                mode = (info.external_attr >> 16) & 0xFFFF
                type_bits = stat.S_IFMT(mode)
                if type_bits not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ReleaseError(f"archive contains a link or special file: {info.filename!r}")
                if info.filename == root + "/":
                    if not info.is_dir() or root_seen:
                        raise ReleaseError("archive root directory is duplicated or malformed")
                    root_seen = True
                    continue
                relative = _member_relative(info.filename, root)
                if relative is None:
                    raise ReleaseError("invalid archive root member")
                if relative in seen:
                    raise ReleaseError(f"duplicate archive member: {relative}")
                seen.add(relative)
                if info.is_dir():
                    continue
            if not root_seen:
                raise ReleaseError("archive root directory is missing")
            archive.extractall(Path(temporary))
        if not extracted.is_dir():
            raise ReleaseError("archive extracted without its root directory")
        verify_package_checksums(extracted)
        check_public_paths(extracted)


def build_package(output_dir: Path, manifest: dict[str, Any]) -> Path:
    files = collect_source(manifest)
    root = archive_root(manifest)
    archive_path = output_dir / f"{root}.zip"
    if archive_path.exists() or archive_path.is_symlink():
        raise ReleaseError(f"refusing to overwrite existing archive: {archive_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-python-release-", dir=output_dir) as temporary:
        stage = Path(temporary) / root
        stage.mkdir()
        copy_to_stage(files, stage)
        check_public_paths(stage)
        write_package_checksums(stage)
        create_archive(stage, archive_path, manifest)
    validate_archive(archive_path, manifest)
    return archive_path


def write_external_checksums(output_dir: Path, archives: list[Path]) -> Path:
    checksum_path = output_dir / CHECKSUMS_NAME
    if checksum_path.exists() or checksum_path.is_symlink():
        raise ReleaseError(f"refusing to overwrite external checksum file: {checksum_path}")
    if not archives:
        raise ReleaseError("no release archives were produced")
    for archive in archives:
        if archive.is_symlink() or not archive.is_file():
            raise ReleaseError(f"external checksum target is not a regular file: {archive}")
    names = [archive.name for archive in archives]
    if len(set(names)) != len(names):
        raise ReleaseError("duplicate archive names in external checksum set")
    lines = [f"{digest(path)}  {path.name}" for path in sorted(archives, key=lambda item: item.name)]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(checksum_path, 0o644)
    expected = {path.name: digest(path) for path in archives}
    actual: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        match = CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ReleaseError("malformed external checksum line")
        checksum, name = match.groups()
        safe_relative(name)
        if name in actual:
            raise ReleaseError(f"duplicate external checksum entry: {name}")
        actual[name] = checksum.lower()
    if actual != expected:
        raise ReleaseError("external checksum coverage mismatch")
    return checksum_path


def build_release(output_dir: Path) -> tuple[Path, Path]:
    manifest = load_manifest()
    output_dir = output_dir.expanduser()
    if output_dir.exists() and output_dir.is_symlink():
        raise ReleaseError(f"refusing symlink output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_archive = output_dir / f"{archive_root(manifest)}.zip"
    expected_checksums = output_dir / CHECKSUMS_NAME
    if expected_archive.exists() or expected_archive.is_symlink():
        raise ReleaseError(f"refusing to overwrite existing archive: {expected_archive}")
    if expected_checksums.exists() or expected_checksums.is_symlink():
        raise ReleaseError(f"refusing to overwrite external checksum file: {expected_checksums}")
    archive_path = build_package(output_dir, manifest)
    checksum_path = write_external_checksums(output_dir, [archive_path])
    return archive_path, checksum_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "dist" / "0.4.0-python",
        help="directory for the new ZIP and external SHA256SUMS",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
        archive_path, checksum_path = build_release(args.output_dir)
    except (ReleaseError, OSError, ValueError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "archive": str(archive_path),
        "external_checksums": str(checksum_path),
        "version": EXPECTED_VERSION,
        "source_equals_runtime": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
