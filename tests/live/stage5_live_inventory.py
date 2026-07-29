"""Manual Stage 5 live DXF inventory check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gebal_cad_normalizer.cad import (
    DxfInventoryError,
    inventory_dxf,
    write_inventory_json,
    write_inventory_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only DXF inventory audit.")
    parser.add_argument("--input", required=True, help="Input .dxf path.")
    parser.add_argument("--json-output", help="Optional explicit JSON report path.")
    parser.add_argument("--markdown-output", help="Optional explicit Markdown report path.")
    args = parser.parse_args()

    try:
        result = inventory_dxf(Path(args.input))
        if args.json_output:
            write_inventory_json(result, Path(args.json_output))
        if args.markdown_output:
            write_inventory_markdown(result, Path(args.markdown_output))
    except DxfInventoryError as exc:
        print(json.dumps({"success": False, "error_code": exc.code.value, "message": str(exc)}, sort_keys=True, separators=(",", ":")))
        return 1

    summary = {
        "success": True,
        "dxf_version": result.dxf_version,
        "units": result.drawing_units,
        "insunits": result.insunits,
        "layer_count": len(result.layers),
        "entity_count": result.total_entity_count,
        "top_entity_types": [item.model_dump(mode="json") for item in result.entity_counts[:10]],
        "extents": result.modelspace_extents,
        "issue_codes": [issue.code for issue in result.issues],
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
