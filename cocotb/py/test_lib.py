# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Shared flow for the cocotb acceptance tests.

Every test runs the same skeleton: bring the environment up, run until the
program reports its result through the signature/tohost handshake, let the
scoreboard catch up with everything retired, then finish the co-simulator
and assert. Tests differ only in which program they run and which agent
knobs they declare on ``IbexCocotbConfig``; the program itself is
responsible for deciding whether it passed (e.g. the irq test checks its
interrupt counters, the error test checks the trap it expects).
"""

import logging
import os
from pathlib import Path

import cocotb

from env import IbexCocotbConfig
from tb_env import bring_up

logger = logging.getLogger("cocotb.test")

# Upper bound for one wait between two handshake writes. The tests write
# their result within a few thousand cycles of the previous signature
# write; five million nanoseconds at a 10ns period leaves plenty of margin
# for the randomised memory timing and the IRQ agent's idle gaps.
RESULT_WRITE_TIMEOUT_NS = 5_000_000
# Upper bound for the scoreboard's catch-up drain: how long one wait may go
# without any progress. The drain re-arms on every processed item, so this
# is a stall bound, not a total budget (see scoreboard.Scoreboard.drain).
DRAIN_STALL_TIMEOUT_NS = 1_000_000


def load_addr_from_plusargs():
    """The load base is a build-flow decision (Makefile), so it arrives as a
    plusarg; the tb uses the same value for the co-simulator backdoor load.
    The value is plain hexadecimal without a 0x prefix (matching the SV %h
    parse on the tb side), so it must be parsed with base 16."""
    assert "ibex_cocotb_load_addr" in cocotb.plusargs, (
        "ibex_cocotb_load_addr plusarg is required (set by the Makefile)")
    return int(cocotb.plusargs["ibex_cocotb_load_addr"], 16)


def bin_path_from_env():
    path = os.environ.get("IBEX_COCOTB_BIN")
    assert path and Path(path).exists(), (
        "IBEX_COCOTB_BIN must point at the test binary")
    return path


async def run_ibex_test(dut, cfg=None):
    """Run one program to its handshake, check the co-simulator and assert.

    The knobs come from ``cfg`` (the tests declare what they need, see
    tests/test_core.py). Raises when the result is missing or not a pass.
    """
    if cfg is None:
        cfg = IbexCocotbConfig()

    env = await bring_up(dut, cfg, bin_path_from_env(),
                         load_addr_from_plusargs())
    mem, monitor, scoreboard = env.mem, env.monitor, env.scoreboard

    result = await mem.wait_for_result(RESULT_WRITE_TIMEOUT_NS)

    # Let the scoreboard catch up with everything retired so far; drain()
    # raises the scoreboard's stored error (a structured mismatch or a
    # transport failure) instead of polling its counters here.
    await scoreboard.drain(DRAIN_STALL_TIMEOUT_NS)

    assert scoreboard.error is None, scoreboard.error
    assert monitor.retired_count > 0, "no instructions retired"
    assert monitor.order_gap is None, (
        "RVFI order jumped {} -> {}: every later comparison is "
        "untrustworthy".format(*monitor.order_gap))

    matched = await scoreboard.finish()
    logger.info("result=%s, retired %d instructions, matched %d, "
                "%d traps, %d loads, %d stores, %d spurious responses, "
                "%d errors, %d irq raises, %d irq-only events, "
                "%d raw irq cycles",
                result, monitor.retired_count, matched, monitor.trap_count,
                mem.load_count, mem.store_count, mem.spurious_count,
                mem.error_count, env.irq.raise_count,
                monitor.irq_only_count, monitor.raw_irq_cycles)

    # The program reported a pass and every compared instruction matched
    # the co-simulator. The matched count may lag the retired count by one
    # or two loop iterations because the tests end in an infinite branch.
    assert result == "pass", (
        "test failed: result={}, last observed write={}".format(
            result, mem.observed_writes[-1] if mem.observed_writes else None))
    return result
