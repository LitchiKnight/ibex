# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Acceptance tests for the cocotb verification environment.

Every test runs a program on the cocotb testbench and checks each retired
instruction against the SpikeCosim co-simulator (dv/cosim, compiled into
the simulation unchanged). The program is selected with
``make sim TEST=<name>`` and passed through the IBEX_COCOTB_BIN environment
variable; the same binary is handed to the co-simulator through the
+ibex_cocotb_bin plusarg, and the image load base address through
+ibex_cocotb_load_addr.

The shared flow lives in py/test_lib.run_ibex_test; the test functions here
only select the agent configuration:

- ``test_rv32ui`` / ``test_rv32mi``: vendored riscv-tests compliance suites.
  The Makefile enables random memory delays, spurious dside responses and
  the IRQ agent for these runs (the programs run with mstatus.MIE cleared,
  so raised interrupt lines are observable through RVFI but never trap).
- ``test_irq``: the dedicated interrupt test, which enables MIE, takes real
  interrupt and NMI traps and self-checks its interrupt counters. Runs with
  the full IRQ agent including the NMI.
- ``test_error``: the dedicated error test, which loads from a poisoned
  address and expects the load access fault. The poisoned address always
  receives an error response from the memory agent.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

import cocotb

from env import IbexCocotbConfig
from test_lib import run_ibex_test

logger = logging.getLogger("cocotb.test")


@cocotb.test()
async def test_rv32ui(dut):
    await run_ibex_test(dut)


@cocotb.test()
async def test_rv32mi(dut):
    await run_ibex_test(dut)


@cocotb.test()
async def test_irq(dut):
    await run_ibex_test(dut)


@cocotb.test()
async def test_error(dut):
    cfg = IbexCocotbConfig()
    # Accesses to the poisoned address always receive an error response;
    # the program expects the resulting load access fault. The address is
    # unmapped in the co-simulator's sparse memory, so Spike faults on it
    # the same way the DUT does.
    await run_ibex_test(dut, error_addrs=(cfg.error_addr,))
