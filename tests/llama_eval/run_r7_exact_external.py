#!/usr/bin/env python3
"""Run R7 exact-contract external-example validation."""

from __future__ import annotations

import sys

from tests.llama_eval.run_r7_external_examples import main


if __name__ == "__main__":
    sys.argv.extend(["--track", "exact"])
    sys.exit(main())
