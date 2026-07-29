"""Tests for Stage 3 source version management."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gebal_cad_normalizer.assets.downloader import DownloadResult
from gebal_cad_normalizer.assets.version_manager import SourceStatus, SourceVersionManager
from gebal_cad_normalizer.exceptions import VersioningError


def _stage(warehouse: Path, name: str, content: bytes, *, revision: str = "rev-1") -> DownloadResult:
    staging = warehouse / "work"
    staging.mkdir(parents=True, exist_ok=True)
    staged_path = staging / name
    staged_path.write_bytes(content)
    return DownloadResult(
        staged_path=staged_path,
        original_filename=name,
        sanitized_filename=name,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        source_url="https://example.invalid/source.dwg",
        media_id="media-1",
        vendor_revision=revision,
        vendor_updated_at="2026-07-21T10:00:00Z",
        content_type="application/acad",
    )


def _manifest(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_first_source_creates_current_and_manifest(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    result = SourceVersionManager(warehouse).update_source("137132M", _stage(warehouse, "first.dwg", b"AC1027-first"))

    source_dir = warehouse / "inventory" / "SKU_137132M" / "source"
    manifest_path = source_dir / "source_manifest.json"

    assert result.status == SourceStatus.CREATED
    assert result.current_path == source_dir / "137132M_source_current.dwg"
    assert result.current_path.read_bytes() == b"AC1027-first"
    assert manifest_path.exists()
    assert (warehouse / "inventory" / "SKU_137132M" / "archive").iterdir()
    assert list((warehouse / "inventory" / "SKU_137132M" / "archive").iterdir()) == []


def test_identical_checksum_returns_unchanged_and_deletes_staged_duplicate(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    manager = SourceVersionManager(warehouse)
    manager.update_source("137132M", _stage(warehouse, "first.dwg", b"AC1027-same"))
    duplicate = _stage(warehouse, "duplicate.dwg", b"AC1027-same")

    result = manager.update_source("137132M", duplicate)

    assert result.status == SourceStatus.UNCHANGED
    assert not duplicate.staged_path.exists()
    assert result.current_path.read_bytes() == b"AC1027-same"
    assert list((warehouse / "inventory" / "SKU_137132M" / "archive").iterdir()) == []


def test_changed_source_archives_current_and_promotes_new(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    manager = SourceVersionManager(warehouse)
    manager.update_source("137132M", _stage(warehouse, "first.dwg", b"AC1027-old", revision="r1"))

    result = manager.update_source("137132M", _stage(warehouse, "second.dwg", b"AC1027-new", revision="r2"))

    assert result.status == SourceStatus.UPDATED
    assert result.current_path.read_bytes() == b"AC1027-new"
    assert result.archived_path is not None
    assert result.archived_path.exists()
    assert result.archived_path.read_bytes() == b"AC1027-old"
    assert "rev_r1" in result.archived_path.name


def test_only_three_archived_versions_are_retained(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    manager = SourceVersionManager(warehouse, archive_limit=3)
    manager.update_source("137132M", _stage(warehouse, "v0.dwg", b"AC1027-v0", revision="r0"))

    for index in range(1, 6):
        manager.update_source("137132M", _stage(warehouse, f"v{index}.dwg", f"AC1027-v{index}".encode(), revision=f"r{index}"))

    archives = sorted((warehouse / "inventory" / "SKU_137132M" / "archive").glob("*_source.dwg"))

    assert len(archives) == 3
    assert b"AC1027-v0" not in {path.read_bytes() for path in archives}
    assert b"AC1027-v1" not in {path.read_bytes() for path in archives}


def test_failed_promotion_preserves_current_and_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gebal_cad_normalizer.assets.version_manager as version_manager

    warehouse = tmp_path / "warehouse"
    manager = SourceVersionManager(warehouse)
    manager.update_source("137132M", _stage(warehouse, "first.dwg", b"AC1027-old", revision="r1"))
    source_dir = warehouse / "inventory" / "SKU_137132M" / "source"
    current_path = source_dir / "137132M_source_current.dwg"
    manifest_path = source_dir / "source_manifest.json"
    before_manifest = manifest_path.read_text(encoding="utf-8")
    changed = _stage(warehouse, "changed.dwg", b"AC1027-new", revision="r2")
    real_replace = version_manager.os.replace

    def failing_replace(src: object, dst: object) -> None:
        if Path(src) == changed.staged_path and Path(dst) == current_path:
            raise OSError("simulated promotion failure")
        real_replace(src, dst)

    monkeypatch.setattr(version_manager.os, "replace", failing_replace)

    with pytest.raises(VersioningError):
        manager.update_source("137132M", changed)

    assert current_path.read_bytes() == b"AC1027-old"
    assert manifest_path.read_text(encoding="utf-8") == before_manifest
    assert list((warehouse / "inventory" / "SKU_137132M" / "archive").glob("*_source.dwg")) == []


def test_manifest_updates_correctly(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    result = SourceVersionManager(warehouse).update_source(
        "137132M",
        _stage(warehouse, "first.dwg", b"AC1027-first", revision="revision-7"),
    )
    manifest_path = warehouse / "inventory" / "SKU_137132M" / "source" / "source_manifest.json"
    manifest = _manifest(manifest_path)

    assert manifest["sku"] == "137132M"
    assert manifest["current_filename"] == "137132M_source_current.dwg"
    assert manifest["sha256"] == result.manifest.sha256
    assert manifest["source_url"] == "https://example.invalid/source.dwg"
    assert manifest["media_id"] == "media-1"
    assert manifest["revision"] == "revision-7"
    assert manifest["vendor_updated_at"] == "2026-07-21T10:00:00Z"
    assert isinstance(manifest["downloaded_at"], str)


def test_source_paths_are_contained_within_warehouse_root(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    outside = tmp_path / "outside.dwg"
    outside.write_bytes(b"AC1027-outside")
    result = DownloadResult(
        staged_path=outside,
        original_filename="outside.dwg",
        sanitized_filename="outside.dwg",
        sha256=hashlib.sha256(b"AC1027-outside").hexdigest(),
        size_bytes=len(b"AC1027-outside"),
    )

    with pytest.raises(VersioningError, match="escapes"):
        SourceVersionManager(warehouse).update_source("../137132M", result)


def test_source_inputs_are_not_mutated(tmp_path: Path) -> None:
    warehouse = tmp_path / "warehouse"
    download = _stage(warehouse, "first.dwg", b"AC1027-first")
    before = download.model_dump()

    SourceVersionManager(warehouse).update_source("SKU WITH SPACES", download)

    assert download.model_dump() == before
    assert (warehouse / "inventory" / "SKU_SKU_WITH_SPACES" / "source" / "SKU_WITH_SPACES_source_current.dwg").exists()
