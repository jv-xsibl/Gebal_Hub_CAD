"""Manual Stage 12 integrated reporting pipeline check.

This script is intentionally outside the default pytest suite. It runs directly
in the workspace and writes only under the explicit output directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gebal_cad_normalizer.reporting import run_reporting_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 12 integrated CAD pipeline packaging.")
    parser.add_argument("--json", required=True, help="Input product JSON path.")
    parser.add_argument("--input", required=True, help="Input .dwg or .dxf path.")
    parser.add_argument("--output-dir", required=True, help="Output root for occurrence package.")
    parser.add_argument("--oda-exe", help="Optional ODA File Converter executable.")
    parser.add_argument("--vendor-profile", default="bluestone_playground", help="Optional built-in layer profile.")
    parser.add_argument("--unit", choices=("mm", "cm", "m", "in"), help="Explicit drawing unit override.")
    parser.add_argument("--export-dwg", action="store_true", help="Export normalized DWG through ODA.")
    parser.add_argument("--allow-overwrite", action="store_true", help="Replace an existing occurrence package.")
    args = parser.parse_args()

    try:
        manifest = run_reporting_pipeline(
            json_path=Path(args.json),
            input_path=Path(args.input),
            output_dir=Path(args.output_dir),
            oda_exe=Path(args.oda_exe) if args.oda_exe else None,
            vendor_profile=args.vendor_profile,
            unit=args.unit,
            export_dwg=args.export_dwg,
            allow_overwrite=args.allow_overwrite,
        )
    except Exception as exc:
        print(json.dumps({"success": False, "type": type(exc).__name__, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    package_path = Path(args.output_dir) / manifest["occurrence_id"]
    summary = {
        "success": manifest.get("source_checksums_unchanged") is True and manifest.get("overall_status") != "fail",
        "overall_status": manifest.get("overall_status"),
        "occurrence_id": manifest.get("occurrence_id"),
        "sku": manifest.get("sku"),
        "package_path": str(package_path),
        "manifest_path": str(package_path / "manifest.json"),
        "report_path": str(package_path / "report.md"),
        "stage_statuses": manifest.get("stage_statuses", {}),
        "issue_counts": manifest.get("issue_counts", {}),
        "source_checksums_unchanged": manifest.get("source_checksums_unchanged"),
        "filename_mismatch": manifest.get("filename_mismatch_evidence", {}).get("mismatch"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["source_checksums_unchanged"] else 1


if __name__ == "__main__":
    sys.exit(main())
