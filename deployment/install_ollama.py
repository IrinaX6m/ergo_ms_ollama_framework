"""Compatibility entry point for the shared core installer."""

import sys
from pathlib import Path

_DEPLOYMENT_DIR = Path(__file__).resolve().parents[3] / 'core' / 'deployment'
if str(_DEPLOYMENT_DIR) not in sys.path:
    sys.path.insert(0, str(_DEPLOYMENT_DIR))

from packages.installers import ollama as _implementation  # noqa: E402

main = _implementation.main

if __name__ == '__main__':
    raise SystemExit(main())
else:
    # Preserve imported helper names and patch targets during the transition.
    sys.modules[__name__] = _implementation
