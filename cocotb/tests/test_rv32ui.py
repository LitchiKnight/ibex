# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""rv32ui acceptance test: run one vendored riscv-tests rv32ui test on the
cocotb testbench and check every retired instruction against the SpikeCosim
co-simulator (dv/cosim, compiled into the simulation unchanged).

The test case is selected with ``make sim TEST=<name>`` and passed to this
module through the IBEX_COCOTB_BIN environment variable; the same binary is
handed to the co-simulator through the +ibex_cocotb_bin plusarg, and the
image load base address through +ibex_cocotb_load_addr.

The test ends when the program reports its result through the signature
handshake or a tohost write (see mem_agent.classify_test_result).
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "py"))

import cocotb
from cocotb.triggers import First, RisingEdge, Timer

from env import IbexCocotbConfig
from mem_agent import classify_test_result
from tb_env import bring_up

logger = logging.getLogger("cocotb.test")

# The rv32ui tests retire only a few hundred instructions; two million
# nanoseconds at a 10ns period leaves plenty of margin for the M1 memory
# timing (three cycles per access).
TIMEOUT_NS = 2_000_000
# Number of clock cycles allowed for the scoreboard to catch up with the
# RVFI monitor after the test has reported its result. Each access costs
# about three cycles and the queue holds a few hundred items at most, so
# 1000 cycles leaves ample margin.
CATCHUP_CYCLES = 1000


@cocotb.test()
async def test_rv32ui(dut):
    cfg = IbexCocotbConfig()

    bin_path = os.environ.get("IBEX_COCOTB_BIN")
    assert bin_path and Path(bin_path).exists(), (
        "IBEX_COCOTB_BIN must point at the test binary")

    # The load base is a build-flow decision (Makefile), so it arrives as a
    # plusarg; the tb uses the same value for the co-simulator backdoor load.
    # The value is plain hexadecimal without a 0x prefix (matching the SV
    # %h parse on the tb side), so it must be parsed with base 16.
    assert "ibex_cocotb_load_addr" in cocotb.plusargs, (
        "ibex_cocotb_load_addr plusarg is required (set by the Makefile)")
    load_addr = int(cocotb.plusargs["ibex_cocotb_load_addr"], 16)

    env = await bring_up(dut, cfg, bin_path, load_addr)
    mem, monitor, scoreboard = env.mem, env.monitor, env.scoreboard

    # Run until the test reports its result. done_event fires on every write
    # to a watched address, but only some of those writes carry a result
    # (the first signature write is a CORE_STATUS marker), so keep waiting
    # until classification succeeds or a full timeout elapses without any
    # new write.
    result = None
    while result is None:
        mem.done_event.clear()
        await First(mem.done_event.wait(), Timer(TIMEOUT_NS, unit="ns"))
        result = classify_test_result(mem.observed_writes,
                                      cfg.signature_addr, cfg.tohost_addr)
        if result is None and not mem.done_event.is_set():
            break
    assert result is not None, (
        "timeout: the test never reported its result (signature 0x{:08x}, "
        "tohost 0x{:08x})".format(cfg.signature_addr, cfg.tohost_addr))

    # Let the scoreboard catch up with everything retired so far. If the
    # scoreboard already died, stop polling: the assertion below must report
    # the real error instead of a misleading "did not catch up".
    caught_up = False
    for _ in range(CATCHUP_CYCLES):
        await RisingEdge(dut.clk_o)
        if scoreboard.error is not None:
            break
        if scoreboard.processed >= monitor.retired_count:
            caught_up = True
            break

    assert scoreboard.error is None, scoreboard.error
    assert caught_up, (
        "scoreboard did not catch up: processed {}, retired {}".format(
            scoreboard.processed, monitor.retired_count))
    assert monitor.retired_count > 0, "no instructions retired"

    matched = await scoreboard.finish()
    logger.info("result=%s, retired %d instructions, matched %d, "
                "%d loads, %d stores",
                result, monitor.retired_count, matched,
                mem.load_count, mem.store_count)

    # M1 acceptance: the test reported a pass and every compared instruction
    # matched the co-simulator. The matched count may lag the retired count by
    # one or two loop iterations because the test ends in an infinite branch.
    assert result == "pass", (
        "test failed: result={}, last observed write={}".format(
            result, mem.observed_writes[-1] if mem.observed_writes else None))
