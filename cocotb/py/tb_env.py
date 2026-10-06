# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Testbench assembly for the cocotb verification environment.

A single ``bring_up`` coroutine owns the knowledge of how the DUT inputs are
initialised, how reset is released and how the memory agent, RVFI monitor
and scoreboard are started and wired together, so that every test only
expresses what it runs instead of how the environment is assembled.
"""

from dataclasses import dataclass
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, Timer

from mem_agent import IbexMemAgent
from rvfi_monitor import RVFIMonitor
from scoreboard import Scoreboard


@dataclass
class Env:
    """The running environment handed back to the test."""

    mem: IbexMemAgent
    monitor: RVFIMonitor
    scoreboard: Scoreboard


async def bring_up(dut, cfg, bin_path, load_addr: int) -> Env:
    """Initialise the DUT inputs, release reset and start the components.

    The clock is generated inside the tb; cocotb only drives the remaining
    stimuli and uses ``clk_o`` as its timebase.
    """
    # Static DUT inputs.
    dut.instr_gnt_i.value = 0
    dut.instr_rvalid_i.value = 0
    dut.instr_rdata_i.value = 0
    dut.instr_rdata_intg_i.value = 0
    dut.instr_err_i.value = 0
    dut.data_gnt_i.value = 0
    dut.data_rvalid_i.value = 0
    dut.data_rdata_i.value = 0
    dut.data_rdata_intg_i.value = 0
    dut.data_err_i.value = 0
    dut.irq_software_i.value = 0
    dut.irq_timer_i.value = 0
    dut.irq_external_i.value = 0
    dut.irq_fast_i.value = 0
    dut.irq_nm_i.value = 0
    dut.debug_req_i.value = 0
    dut.cmd_valid.value = 0
    dut.cmd_op.value = 0
    for reg in (dut.cmd_a0, dut.cmd_a1, dut.cmd_a2, dut.cmd_a3, dut.cmd_a4,
                dut.cmd_a5, dut.cmd_a6):
        reg.value = 0

    # Reset.
    dut.rst_ni.value = 0
    await Timer(100, unit="ns")
    dut.rst_ni.value = 1
    await RisingEdge(dut.clk_o)

    # Components.
    mem = IbexMemAgent(dut, cfg.tohost_addr, cfg.signature_addr)
    mem.load_bin(Path(bin_path), load_addr)
    cocotb.start_soon(mem.run())

    monitor = RVFIMonitor(dut)
    cocotb.start_soon(monitor.run())

    scoreboard = Scoreboard(dut)
    await scoreboard.init_cosim()
    mem.on_access = scoreboard.notify_dside
    cocotb.start_soon(scoreboard.run(monitor))

    return Env(mem=mem, monitor=monitor, scoreboard=scoreboard)
