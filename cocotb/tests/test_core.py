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

Each test declares the agent knobs it needs on IbexCocotbConfig (the
Makefile plusargs may still override them); the shared flow lives in
py/test_lib.run_ibex_test:

- ``test_rv32ui`` / ``test_rv32mi``: vendored riscv-tests compliance suites,
  run with the IRQ agent on (the programs run with mstatus.MIE cleared, so
  raised interrupt lines are observable through RVFI but never trap).
- ``test_irq``: the dedicated interrupt test, which enables MIE, takes real
  interrupt traps and self-checks its interrupt counters. Runs with the
  IRQ agent on; the NMI line stays off on this tb (WritebackStage=0, see
  py/irq_agent.py).
- ``test_error``: the dedicated error test, which loads from a poisoned
  address and expects the load access fault. The poisoned address has one
  definition (the Makefile's IBEX_ERROR_ADDR): it is compiled into the
  program as -DERROR_ADDR and passed to the memory agent through the
  +ibex_cocotb_error_addrs plusarg, so this test declares no knobs.
- ``test_random``: the generated random instruction program
  (gen/instr_gen.py, M3). The program self-checks its trap count and the
  handshake result; it runs with every agent off (the stream holds
  mstatus.MIE clear and takes only its deliberately injected illegal
  instructions).
"""

import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

import cocotb

from env import IbexCocotbConfig
from test_lib import run_ibex_test

logger = logging.getLogger("cocotb.test")


@cocotb.test()
async def test_rv32ui(dut):
    await run_ibex_test(dut, replace(IbexCocotbConfig(), irq_enable=True))


@cocotb.test()
async def test_rv32mi(dut):
    await run_ibex_test(dut, replace(IbexCocotbConfig(), irq_enable=True))


@cocotb.test()
async def test_irq(dut):
    await run_ibex_test(dut, replace(IbexCocotbConfig(), irq_enable=True))


@cocotb.test()
async def test_error(dut):
    await run_ibex_test(dut)


@cocotb.test()
async def test_random(dut):
    await run_ibex_test(dut)
