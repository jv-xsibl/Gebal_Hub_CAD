"""Manual Stage 9 DXF layer rewriting check.

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
    ClassificationResult,
    LayerRewriteConfig,
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    rewrite_layers,
)
from gebal_cad_normalizer.exceptions import CadNormalizerError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_classification(path: Path) -> ClassificationResult:
    return ClassificationResult.model_validate(json.loads(path.read_text(encoding="utf-8")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Stage 9 normalized DXF layer rewriting.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--output", required=True, help="Explicit output .dxf path.")
    parser.add_argument("--classification-json", help="Optional Stage 8 classification JSON path.")
    parser.add_argument("--confidence-threshold", type=float, default=0.62, help="Minimum confidence required for confident operational layer movement.")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        before = _sha256(input_path)
        if args.classification_json:
            classification = _load_classification(Path(args.classification_json))
        else:
            inventory = inventory_dxf(input_path)
            canonical = canonicalize_dxf(input_path)
            classification = classify_layers(inventory, canonical)
        result = rewrite_layers(
            input_path,
            classification,
            output_path,
            LayerRewriteConfig(confidence_threshold=args.confidence_threshold),
        )
        after = _sha256(input_path)
    except (CadNormalizerError, OSError, ValueError) as exc:
        print(json.dumps({"success": False, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    issue_counts = Counter(issue.code for issue in result.issues)
    summary = {
        "success": result.preservation_checks.source_checksum_unchanged
        and result.preservation_checks.entity_count_preserved
        and result.preservation_checks.modelspace_extents_preserved
        and before == after,
        "source_path": str(result.source_path),
        "output_path": str(result.output_path),
        "source_sha256": result.source_sha256_before,
        "output_sha256": result.output_sha256,
        "moved_entity_count": result.moved_entity_count,
        "unchanged_entity_count": result.unchanged_entity_count,
        "review_required_entity_count": result.review_required_entity_count,
        "entity_totals_before": result.entity_totals_before.model_dump(),
        "entity_totals_after": result.entity_totals_after.model_dump(),
        "issue_codes": dict(sorted(issue_counts.items())),
        "source_checksum_unchanged": before == after,
        "classification_json_used": args.classification_json is not None,
        "confidence_threshold": args.confidence_threshold,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
