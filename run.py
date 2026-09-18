"""Convenience launcher: `python run.py [run|web|process <file>|sample]`.

Identical to `python -m kyc_grabber`, but works from any working directory because
the project root is added to ``sys.path`` here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kyc_grabber.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
