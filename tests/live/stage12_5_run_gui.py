"""Manual Stage 12.5 operator GUI launcher.

This script is intentionally outside the default pytest suite. It runs directly
in the workspace and uses the same Stage 12 reporting pipeline as the CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gebal_cad_normalizer.gui import main


if __name__ == "__main__":
    raise SystemExit(main())
