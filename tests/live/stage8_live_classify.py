"""Manual Stage 8 live layer classification check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from gebal_cad_normalizer.cad import (
    CanonicalizationError,
    DxfInventoryError,
    canonicalize_dxf,
    LayerClassificationConfig,
    classify_layers,
    inventory_dxf,
    write_classification_json,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only Stage 8 deterministic layer classification.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--json-output", help="Optional deterministic classification JSON output path.")
    parser.add_argument("--skip-canonical", action="store_true", help="Use Stage 5 inventory only and skip optional Stage 6 canonical evidence.")
    parser.add_argument("--vendor-profile", help="Optional built-in vendor layer profile, for example bluestone_playground.")
    args = parser.parse_args()

    input_path = Path(args.input)
    try:
        before = _sha256(input_path)
        inventory = inventory_dxf(input_path)
        canonical = None if args.skip_canonical else canonicalize_dxf(input_path)
        config = LayerClassificationConfig(vendor_profile=args.vendor_profile) if args.vendor_profile else None
        result = classify_layers(inventory, canonical, config)
        if args.json_output:
            write_classification_json(result, Path(args.json_output))
        after = _sha256(input_path)
    except (DxfInventoryError, CanonicalizationError, OSError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    issue_counts = Counter(issue.code for issue in result.issues)
    review_layers = [layer.original_layer_name for layer in result.layers if layer.assigned_role in {"ambiguous", "review_required"}]
    confidences = [layer.confidence for layer in result.layers]
    summary = {
        "success": before == after,
        "layer_count": result.layer_count,
        "role_counts": result.role_counts,
        "ambiguous_or_review_layers": review_layers,
        "confidence": {
            "min": min(confidences) if confidences else None,
            "max": max(confidences) if confidences else None,
            "average": round(sum(confidences) / len(confidences), 4) if confidences else None,
        },
        "issue_codes": dict(sorted(issue_counts.items())),
        "source_checksum_unchanged": before == after,
        "json_output": args.json_output,
        "canonical_evidence_used": canonical is not None,
        "vendor_profile": args.vendor_profile,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if before == after else 1


if __name__ == "__main__":
    sys.exit(main())
