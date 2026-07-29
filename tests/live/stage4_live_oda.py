"""Manual Stage 4 live ODA conversion check.

This script is intentionally outside the default pytest suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gebal_cad_normalizer.cad import OdaConversionRequest, OdaConverter, OdaFileConverterError


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one live ODA File Converter conversion.")
    parser.add_argument("--oda-exe", required=True, help="Path to ODA File Converter executable.")
    parser.add_argument("--input", required=True, help="Input .dwg or .dxf path.")
    parser.add_argument("--output", required=True, help="Output .dxf or .dwg path.")
    parser.add_argument("--target-version", default="R2013", help="ODA target CAD version, default R2013.")
    parser.add_argument("--timeout", type=float, default=120.0, help="Timeout in seconds.")
    args = parser.parse_args()

    request = OdaConversionRequest(
        source_path=Path(args.input),
        destination_path=Path(args.output),
        oda_executable_path=Path(args.oda_exe),
        target_version=args.target_version,
        timeout_seconds=args.timeout,
    )

    try:
        result = OdaConverter(Path(args.oda_exe)).convert(request)
    except OdaFileConverterError as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "error_code": exc.code.value,
                    "message": str(exc),
                    "exit_code": exc.exit_code,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                    "elapsed_seconds": exc.elapsed_seconds,
                },
                separators=(",", ":"),
            )
        )
        return 1

    output_path = Path(args.output)
    output_ok = output_path.is_file() and output_path.stat().st_size > 0
    print(
        json.dumps(
            {
                "success": output_ok,
                "source_path": str(result.source_path),
                "destination_path": str(result.destination_path),
                "direction": result.direction.value,
                "requested_cad_version": result.requested_cad_version,
                "exit_code": result.exit_code,
                "elapsed_seconds": result.elapsed_seconds,
                "output_size_bytes": result.output_size_bytes,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            separators=(",", ":"),
        )
    )
    return 0 if output_ok else 1


if __name__ == "__main__":
    sys.exit(main())
