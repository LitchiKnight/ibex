# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Single authority for the per-test coverage database path (P1-3).

py/test_lib.py (the writer) and run.py (the regression merge) both derive
the per-test coverage XML path from here, so the naming rule can never
silently drift between the two sides. This module must stay
dependency-free: run.py imports it outside the cocotb simulator.
"""

from pathlib import Path


def coverage_xml_path(out_dir, suite, test):
    """Path of the per-test coverage database (matches the bin stem)."""
    return Path(out_dir) / f"coverage_{suite}_{test}.xml"
