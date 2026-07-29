"""Manual live Stage 3 integration check for Bluestone 137132M.

This script intentionally is not named ``test_*.py`` because it downloads a
real vendor DWG over the network. Run manually from the repository root:

    python tests/live/stage3_live_137132m.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "137132M_raw.json"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gebal_cad_normalizer.adapters import BluestoneAdapter  # noqa: E402
from gebal_cad_normalizer.assets import CadAssetDownloader, SourceStatus, SourceVersionManager  # noqa: E402


def main() -> int:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(f"Required live fixture is missing: {FIXTURE_PATH}")

    payload = _read_json(FIXTURE_PATH)
    adapter_result = BluestoneAdapter().parse(payload)
    if adapter_result.issues:
        raise AssertionError(f"BluestoneAdapter returned issues: {[issue.model_dump() for issue in adapter_result.issues]}")
    if adapter_result.request is None:
        raise AssertionError("BluestoneAdapter did not return a CadProcessingRequest.")

    request = adapter_result.request
    asset = request.top_view_cad
    _assert_selected_top_view_2dmodel_dwg(asset.model_dump())

    with tempfile.TemporaryDirectory(prefix="gebal_stage3_live_") as temp_dir:
        warehouse = Path(temp_dir) / "warehouse"
        staging = warehouse / "staging"
        downloader = CadAssetDownloader(staging)
        manager = SourceVersionManager(warehouse)

        first_download = downloader.stage(asset)
        first_staged_path = first_download.staged_path
        first = manager.update_source(request.product.sku, first_download)

        manifest_path = first.current_path.parent / "source_manifest.json"
        archive_dir = warehouse / "inventory" / f"SKU_{request.product.sku}" / "archive"
        manifest = _read_json(manifest_path)
        current_checksum = _sha256(first.current_path)

        assert first.status == SourceStatus.CREATED
        assert first.current_path.exists()
        assert manifest_path.exists()
        assert manifest["sha256"] == first_download.sha256
        assert current_checksum == first_download.sha256
        assert manifest["sku"] == request.product.sku
        assert manifest["media_id"] == asset.media_id
        assert manifest["revision"] == "4.1"
        assert manifest["vendor_updated_at"] == asset.vendor_updated_at
        assert first_download.original_filename == asset.file_name
        assert manifest["source_url"] == asset.url
        assert not first_staged_path.exists()

        second_download = downloader.stage(asset)
        second_staged_path = second_download.staged_path
        second_download_size = second_download.size_bytes
        second = manager.update_source(request.product.sku, second_download)
        archives_after_second = sorted(archive_dir.glob("*_source.dwg"))

        assert second.status == SourceStatus.UNCHANGED
        assert not second_staged_path.exists()
        assert archives_after_second == []
        assert _sha256(second.current_path) == current_checksum

        report = {
            "selected_asset": {
                "sku": request.product.sku,
                "asset_name": asset.asset_name,
                "description": asset.description,
                "file_name": asset.file_name,
                "file_type": asset.file_type,
                "media_id": asset.media_id,
                "revision": asset.vendor_revision,
                "updatedAt": asset.vendor_updated_at,
                "source_url": asset.url,
                "purpose": asset.purpose,
                "vendor_asset_classification": asset.vendor_asset_classification,
            },
            "downloaded_size_bytes": first_download.size_bytes,
            "second_downloaded_size_bytes": second_download_size,
            "paths_created": {
                "temporary_warehouse": str(warehouse),
                "current_dwg": str(first.current_path),
                "source_manifest": str(manifest_path),
                "archive_dir": str(archive_dir),
                "first_staged_path_deleted": str(first_staged_path),
                "second_staged_path_deleted": str(second_staged_path),
            },
            "checksum_sha256": current_checksum,
            "first_run_status": first.status.value,
            "second_run_status": second.status.value,
            "duplicate_staged_download_deleted": not second_staged_path.exists(),
            "archive_files_after_second_run": [str(path) for path in archives_after_second],
        }
        print(json.dumps(report, indent=2, sort_keys=True))

    return 0


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_selected_top_view_2dmodel_dwg(asset: dict[str, Any]) -> None:
    combined_text = " ".join(
        str(asset.get(key) or "")
        for key in (
            "asset_name",
            "description",
            "document_information",
            "purpose",
            "vendor_asset_classification",
            "file_name",
        )
    ).lower()

    assert (asset.get("file_name") or "").lower().endswith(".dwg")
    assert "top view" in combined_text
    assert "2dmodel" in combined_text or "2d model" in combined_text
    assert "side view" not in combined_text
    assert "product model" not in combined_text
    assert asset.get("vendor_revision") == "4.1"
    assert asset.get("url")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
