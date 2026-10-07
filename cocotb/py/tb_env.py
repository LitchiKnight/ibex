# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Testbench assembly for the cocotb verification environment.

A single ``bring_up`` coroutine owns the knowledge of how the DUT inputs are
initialised, how reset is released and how the memory agent, IRQ agent,
RVFI monitor and scoreboard are started and wired together, so that every
test only expresses what it runs instead of how the environment is
assembled.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, Timer

from env import IbexCocotbConfig
from irq_agent import IbexIrqAgent, IrqAgentConfig
from mem_agent import IbexMemAgent, MemAgentConfig
from rvfi_monitor import RVFIMonitor
from scoreboard import Scoreboard

logger = logging.getLogger("cocotb.tb_env")


@dataclass
class Env:
    """The running environment handed back to the test."""

    mem: IbexMemAgent
    irq: IbexIrqAgent
    monitor: RVFIMonitor
    scoreboard: Scoreboard


def _plusarg(name, default):
    """Read a runtime knob passed by the Makefile as a plusarg; absent means
    the default (so the flow stays usable without Makefile support)."""
    if name not in cocotb.plusargs:
        return default
    return cocotb.plusargs[name] != "0"


async def bring_up(dut, cfg: IbexCocotbConfig, bin_path, load_addr: int,
                   error_addrs=()) -> Env:
    """Initialise the DUT inputs, release reset and start the components.

    The clock is generated inside the tb; cocotb only drives the remaining
    stimuli and uses ``clk_o`` as its timebase. The randomisation knobs
    arrive as Makefile plusargs (single flow for both fixed and randomised
    runs).
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
    spurious = cfg.spurious_response or _plusarg("ibex_cocotb_spurious_resp",
                                                 False)
    # The core only gates stray bus responses when SecureIbex is set
    # (ibex_core.sv g_check_mem_response); the UVM base test applies the
    # same rule (spurious responses are disabled for non-secure configs).
    # With SecureIbex=0 a spurious response would corrupt the core state.
    if spurious and not int(dut.secure_ibex_o.value):
        logger.warning("mem agent: spurious responses forced off (the DUT "
                       "is not a secure configuration and would not gate "
                       "stray responses)")
        spurious = False

    # +ibex_cocotb_zero_delays=1 forces every delay to zero, =0 forces the
    # random distribution; absent leaves the UVM default (50% of runs pick
    # zero delays).
    if "ibex_cocotb_zero_delays" in cocotb.plusargs:
        zero_delay_pct = (
            100 if cocotb.plusargs["ibex_cocotb_zero_delays"] != "0" else 0)
    else:
        zero_delay_pct = 50

    mem_cfg = MemAgentConfig(
        enable_spurious_response=spurious,
        zero_delay_pct=zero_delay_pct,
        error_addrs=tuple(error_addrs),
    )
    mem = IbexMemAgent(dut, mem_cfg, cfg.tohost_addr, cfg.signature_addr)
    mem.load_bin(Path(bin_path), load_addr)
    cocotb.start_soon(mem.run())

    irq_cfg = IrqAgentConfig(
        enable=cfg.irq_enable or _plusarg("ibex_cocotb_irq", False),
        nmi_enabled=cfg.irq_nmi_enable or _plusarg("ibex_cocotb_irq_nmi",
                                                   False),
    )
    irq = IbexIrqAgent(dut, irq_cfg)
    cocotb.start_soon(irq.run())

    monitor = RVFIMonitor(dut)
    cocotb.start_soon(monitor.run())

    scoreboard = Scoreboard(dut)
    await scoreboard.init_cosim()
    mem.on_access = scoreboard.notify_dside
    cocotb.start_soon(scoreboard.run(monitor))

    return Env(mem=mem, irq=irq, monitor=monitor, scoreboard=scoreboard)
