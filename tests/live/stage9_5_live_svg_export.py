"""Manual Stage 9.5 layer SVG export check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from gebal_cad_normalizer.cad import SvgExportConfig, export_layer_svgs
from gebal_cad_normalizer.exceptions import CadNormalizerError


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 9.5 per-layer CAD SVG export.")
    parser.add_argument("--input", required=True, help="Input .dxf or .dwg path.")
    parser.add_argument("--output-dir", required=True, help="Explicit output directory for manifest, combined SVG, and layer SVGs.")
    parser.add_argument("--oda-exe", help="ODA File Converter executable path for .dwg input.")
    parser.add_argument("--combined", action="store_true", help="Write combined.svg in addition to one SVG per layer.")
    parser.add_argument("--monochrome", action="store_true", help="Render all geometry in monochrome.")
    parser.add_argument("--background", choices=["transparent", "white"], default="transparent", help="SVG background mode.")
    args = parser.parse_args()

    try:
        result = export_layer_svgs(
            Path(args.input),
            Path(args.output_dir),
            oda_executable_path=Path(args.oda_exe) if args.oda_exe else None,
            config=SvgExportConfig(
                include_combined=bool(args.combined),
                monochrome=bool(args.monochrome),
                background=args.background,
            ),
        )
    except (CadNormalizerError, OSError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    issue_counts = Counter(issue.code for issue in result.warnings)
    for layer in result.layers:
        issue_counts.update(issue.code for issue in layer.warnings)
    summary = {
        "success": result.source_checksum_unchanged and not any(issue.severity == "fail" for issue in result.warnings),
        "source_path": str(result.source_file),
        "source_sha256": result.source_sha256,
        "output_dir": str(result.output_dir),
        "manifest_path": str(result.manifest_path),
        "combined_svg_path": str(result.combined_svg_path) if result.combined_svg_path else None,
        "combined_exported": result.combined_exported,
        "layer_count": len(result.layers),
        "rendered_count": sum(layer.rendered_count for layer in result.layers),
        "skipped_count": sum(layer.skipped_count for layer in result.layers),
        "issue_codes": dict(sorted(issue_counts.items())),
        "source_checksum_unchanged": result.source_checksum_unchanged,
        "ai_used": result.ai_used,
        "background": args.background,
        "monochrome": bool(args.monochrome),
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
