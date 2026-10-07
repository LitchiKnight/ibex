# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Shared flow for the cocotb acceptance tests.

Every test runs the same skeleton: bring the environment up, run until the
program reports its result through the signature/tohost handshake, let the
scoreboard catch up with everything retired, then finish the co-simulator
and assert. Tests differ only in which program they run and which agent
knobs they enable; the program itself is responsible for deciding whether
it passed (e.g. the irq test checks its interrupt counters, the error test
checks the trap it expects).
"""

import logging
import os
from pathlib import Path

import cocotb
from cocotb.triggers import First, RisingEdge, Timer

from env import IbexCocotbConfig
from mem_agent import classify_test_result
from tb_env import bring_up

logger = logging.getLogger("cocotb.test")

# The tests retire only a few hundred to a few thousand instructions; five
# million nanoseconds at a 10ns period leaves plenty of margin for the
# randomised memory timing (tens of cycles per access) and the IRQ agent's
# idle gaps.
TIMEOUT_NS = 5_000_000
# Number of clock cycles allowed for the scoreboard to catch up with the
# RVFI monitor after the test has reported its result. Randomised delays
# stretch each access, and the queue can hold a few hundred items, so a
# generous margin is used.
CATCHUP_CYCLES = 50_000


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


async def run_ibex_test(dut, error_addrs=()):
    """Run one program to its handshake, check the co-simulator and assert.

    Returns the classification of the observed handshake writes ("pass" or
    "fail"); the assertions below fail the test when the result is missing
    or not a pass.
    """
    cfg = IbexCocotbConfig()
    env = await bring_up(dut, cfg, bin_path_from_env(),
                         load_addr_from_plusargs(), error_addrs)
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
                "%d loads, %d stores, %d spurious responses, %d errors, "
                "%d irq raises, %d irq-only events",
                result, monitor.retired_count, matched,
                mem.load_count, mem.store_count, mem.spurious_count,
                mem.error_count, env.irq.raise_count,
                monitor.irq_only_count)

    # The program reported a pass and every compared instruction matched
    # the co-simulator. The matched count may lag the retired count by one
    # or two loop iterations because the tests end in an infinite branch.
    assert result == "pass", (
        "test failed: result={}, last observed write={}".format(
            result, mem.observed_writes[-1] if mem.observed_writes else None))
    return result
