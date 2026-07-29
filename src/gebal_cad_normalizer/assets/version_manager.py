"""Source-file version management for staged CAD assets."""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import Field

from gebal_cad_normalizer.assets.downloader import DownloadResult
from gebal_cad_normalizer.exceptions import VersioningError
from gebal_cad_normalizer.models import StrictModel

_SAFE_TOKEN_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class SourceStatus(str, Enum):
    """Source update outcomes produced by the version manager."""

    CREATED = "created"
    UNCHANGED = "unchanged"
    UPDATED = "updated"


class SourceManifest(StrictModel):
    """Manifest describing the currently promoted vendor source file."""

    sku: str = Field(min_length=1)
    current_filename: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    source_url: str | None = None
    media_id: str | None = None
    revision: str | None = None
    vendor_updated_at: str | int | None = None
    downloaded_at: str = Field(min_length=1)


class VersionUpdateResult(StrictModel):
    """Result of promoting, rejecting, or creating a managed source."""

    status: SourceStatus
    manifest: SourceManifest
    current_path: Path
    archived_path: Path | None = None
    deleted_archives: tuple[Path, ...] = ()


class SourceVersionManager:
    """Maintain one current source DWG and a bounded source archive."""

    def __init__(self, warehouse_root: Path, *, archive_limit: int = 3) -> None:
        if archive_limit < 0:
            raise ValueError("archive_limit must not be negative")
        self.warehouse_root = Path(warehouse_root)
        self.archive_limit = archive_limit

    def update_source(self, sku: str, download: DownloadResult) -> VersionUpdateResult:
        """Promote a staged source when its checksum differs from current."""

        sku_token = _safe_token(sku)
        paths = _paths(self.warehouse_root, sku_token)
        _ensure_contained(self.warehouse_root, *paths.values(), download.staged_path)
        paths["source_dir"].mkdir(parents=True, exist_ok=True)
        paths["archive_dir"].mkdir(parents=True, exist_ok=True)

        current_path = paths["current"]
        manifest_path = paths["manifest"]
        current_manifest: SourceManifest | None = None
        if current_path.exists() and manifest_path.exists():
            current_manifest = _read_manifest(manifest_path)
            if current_manifest.sha256 == download.sha256:
                download.staged_path.unlink(missing_ok=True)
                return VersionUpdateResult(status=SourceStatus.UNCHANGED, manifest=current_manifest, current_path=current_path)

        new_manifest = SourceManifest(
            sku=sku,
            current_filename=current_path.name,
            sha256=download.sha256,
            source_url=download.source_url,
            media_id=download.media_id,
            revision=download.vendor_revision,
            vendor_updated_at=download.vendor_updated_at,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        )

        if not current_path.exists():
            self._promote_first(download.staged_path, current_path, manifest_path, new_manifest)
            return VersionUpdateResult(status=SourceStatus.CREATED, manifest=new_manifest, current_path=current_path)

        archived_path = self._promote_changed(download.staged_path, current_path, manifest_path, paths["archive_dir"], new_manifest, current_manifest)
        deleted = self._trim_archives(paths["archive_dir"])
        return VersionUpdateResult(
            status=SourceStatus.UPDATED,
            manifest=new_manifest,
            current_path=current_path,
            archived_path=archived_path,
            deleted_archives=tuple(deleted),
        )

    def _promote_first(self, staged_path: Path, current_path: Path, manifest_path: Path, manifest: SourceManifest) -> None:
        temp_manifest = _manifest_temp_path(manifest_path)
        try:
            _write_manifest(temp_manifest, manifest)
            os.replace(staged_path, current_path)
            os.replace(temp_manifest, manifest_path)
        except Exception as exc:
            _cleanup_path(temp_manifest)
            _cleanup_path(current_path)
            _cleanup_path(staged_path)
            raise VersioningError(f"Failed to promote first source file: {exc}") from exc

    def _promote_changed(
        self,
        staged_path: Path,
        current_path: Path,
        manifest_path: Path,
        archive_dir: Path,
        manifest: SourceManifest,
        archived_manifest: SourceManifest | None,
    ) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        revision = _safe_token((archived_manifest.revision if archived_manifest is not None else None) or "unknown")
        final_archive = archive_dir / f"{timestamp}_rev_{revision}_source.dwg"
        pending_archive = archive_dir / f".pending_{final_archive.name}"
        temp_manifest = _manifest_temp_path(manifest_path)
        old_manifest_text = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else None
        current_replaced = False
        manifest_replaced = False

        try:
            _write_manifest(temp_manifest, manifest)
            shutil.copy2(current_path, pending_archive)
            os.replace(staged_path, current_path)
            current_replaced = True
            os.replace(temp_manifest, manifest_path)
            manifest_replaced = True
            os.replace(pending_archive, final_archive)
            return final_archive
        except Exception as exc:
            if current_replaced and pending_archive.exists():
                os.replace(pending_archive, current_path)
            elif pending_archive.exists():
                _cleanup_path(pending_archive)
            if manifest_replaced and old_manifest_text is not None:
                manifest_path.write_text(old_manifest_text, encoding="utf-8")
            _cleanup_path(temp_manifest)
            _cleanup_path(staged_path)
            raise VersioningError(f"Failed to promote changed source file: {exc}") from exc

    def _trim_archives(self, archive_dir: Path) -> list[Path]:
        archives = sorted(
            [path for path in archive_dir.glob("*_source.dwg") if path.is_file() and not path.name.startswith(".pending_")],
            key=lambda path: path.name,
            reverse=True,
        )
        deleted: list[Path] = []
        for old_path in archives[self.archive_limit :]:
            old_path.unlink()
            deleted.append(old_path)
        return deleted


def _paths(warehouse_root: Path, sku_token: str) -> dict[str, Path]:
    product_dir = Path(warehouse_root) / "inventory" / f"SKU_{sku_token}"
    source_dir = product_dir / "source"
    archive_dir = product_dir / "archive"
    return {
        "product_dir": product_dir,
        "source_dir": source_dir,
        "archive_dir": archive_dir,
        "current": source_dir / f"{sku_token}_source_current.dwg",
        "manifest": source_dir / "source_manifest.json",
    }


def _safe_token(value: str) -> str:
    token = _SAFE_TOKEN_PATTERN.sub("_", value.strip()).strip("._")
    if not token:
        raise VersioningError("SKU or revision cannot be empty after sanitization.")
    return token


def _ensure_contained(root: Path, *paths: Path) -> None:
    root_resolved = root.resolve()
    for path in paths:
        resolved = path.resolve()
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise VersioningError(f"Path escapes warehouse root: {path}")


def _read_manifest(path: Path) -> SourceManifest:
    try:
        return SourceManifest(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise VersioningError(f"Could not read source manifest: {exc}") from exc


def _write_manifest(path: Path, manifest: SourceManifest) -> None:
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def _manifest_temp_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f".{manifest_path.name}.tmp")


def _cleanup_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
