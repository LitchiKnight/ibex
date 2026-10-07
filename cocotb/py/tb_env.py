# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Testbench assembly for the cocotb verification environment.

A single ``bring_up`` coroutine owns the knowledge of how the DUT inputs are
initialised, how reset is released and how the memory agent, IRQ agent,
RVFI monitor and scoreboard are assembled, so that every test only
expresses what it runs instead of how the environment is assembled.

The tests declare their knobs on ``IbexCocotbConfig`` (see env.py); the
plusargs only override them, and each override is applied in exactly one
place below.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, Timer

from cosim import Cosim
from cosim_channel import CosimChannel
from env import IbexCocotbConfig
from irq_agent import IbexIrqAgent, IrqAgentConfig
from mem_agent import IbexMemAgent, MemAgentConfig, TestHandshake
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
    coverage: IbexCoverage | None


def _bool_plusarg(name, default):
    """Read a boolean knob passed by the Makefile as a plusarg; absent means
    the default. When the plusarg is given it overrides the test's choice."""
    if name not in cocotb.plusargs:
        return default
    return cocotb.plusargs[name] != "0"


def _zero_delay_pct():
    """Three-state knob: absent keeps the UVM default (50% of runs pick zero
    delays); ``=0`` forces the random distribution; any other value forces
    every delay to zero."""
    if "ibex_cocotb_zero_delays" not in cocotb.plusargs:
        return 50
    return 100 if cocotb.plusargs["ibex_cocotb_zero_delays"] != "0" else 0


def _error_addrs(cfg: IbexCocotbConfig):
    """Poisoned addresses for the memory agent: the test-declared set plus
    the ``+ibex_cocotb_error_addrs`` plusarg. The Makefile passes the
    dedicated error test's poisoned address through that plusarg (plain
    hexadecimal, comma-separated) so the value and the -DERROR_ADDR the
    test is compiled with share one definition (the Makefile variable)."""
    addrs = list(cfg.error_addrs)
    plusarg = cocotb.plusargs.get("ibex_cocotb_error_addrs")
    if plusarg:
        addrs.extend(int(token, 16) for token in plusarg.split(","))
    return tuple(addrs)


def _resolve_mem_cfg(cfg: IbexCocotbConfig, dut) -> MemAgentConfig:
    """Derive the memory agent configuration from the test-declared knobs
    and the plusargs.

    The spurious-response feature is forced off on non-secure
    configurations: the core only gates stray bus responses when SecureIbex
    is set (ibex_core.sv g_check_mem_response), and the UVM base test
    applies the same rule."""
    spurious = _bool_plusarg("ibex_cocotb_spurious_resp",
                             cfg.spurious_response)
    if spurious and not int(dut.secure_ibex_o.value):
        logger.warning("mem agent: spurious responses forced off (the DUT "
                       "is not a secure configuration and would not gate "
                       "stray responses)")
        spurious = False
    return MemAgentConfig(enable_spurious_response=spurious,
                          zero_delay_pct=_zero_delay_pct(),
                          error_addrs=_error_addrs(cfg))


def _resolve_irq_cfg(cfg: IbexCocotbConfig) -> IrqAgentConfig:
    return IrqAgentConfig(
        enable=_bool_plusarg("ibex_cocotb_irq", cfg.irq_enable),
        nmi_enabled=_bool_plusarg("ibex_cocotb_irq_nmi",
                                  cfg.irq_nmi_enable))


def _init_inputs(dut):
    """Drive every DUT input before reset is released. The IRQ agent owns
    its own lines and initialises them itself; everything else is
    initialised here."""
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
    dut.debug_req_i.value = 0
    dut.cmd_valid.value = 0
    dut.cmd_op.value = 0
    for reg in (dut.cmd_a0, dut.cmd_a1, dut.cmd_a2, dut.cmd_a3, dut.cmd_a4,
                dut.cmd_a5, dut.cmd_a6):
        reg.value = 0


async def bring_up(dut, cfg: IbexCocotbConfig, bin_path, load_addr: int) -> Env:
    """Initialise the DUT inputs, release reset and start the components.

    The clock is generated inside the tb; cocotb only drives the remaining
    stimuli and uses ``clk_o`` as its timebase. The environment is fully
    assembled and wired before any component starts: nothing may observe
    the bus while the wiring is incomplete.
    """
    _init_inputs(dut)

    # Reset.
    dut.rst_ni.value = 0
    await Timer(100, unit="ns")
    dut.rst_ni.value = 1
    await RisingEdge(dut.clk_o)

    # Assemble the environment completely, then start everything at once.
    # The command channel is shared: the scoreboard steps and the memory
    # agent's access notifications are serialised by the channel's lock.
    channel = CosimChannel(dut)
    cosim = Cosim(channel)
    # The coverage model observes the monitor and the memory agent through
    # their callbacks; it has no effect on the comparison flow. The import
    # is lazy so a missing optional cocotb-coverage installation only
    # fails the runs that ask for coverage.
    coverage = None
    if _bool_plusarg("ibex_cocotb_cov", cfg.coverage_enable):
        from coverage import IbexCoverage
        coverage = IbexCoverage()
    monitor = RVFIMonitor(dut, on_retire=coverage.on_retire if coverage
                          else None)
    handshake = TestHandshake(cfg.signature_addr, cfg.tohost_addr)
    mem = IbexMemAgent(dut, _resolve_mem_cfg(cfg, dut), handshake,
                       on_access=cosim.notify_dside,
                       on_bus_event=coverage.on_bus_event if coverage
                       else None)
    # The scoreboard reads the agent's pending instruction-side error
    # before each trap step (the UVM ifetch queue equivalent).
    scoreboard = Scoreboard(cosim, monitor, iside_error_source=mem)
    irq = IbexIrqAgent(dut, _resolve_irq_cfg(cfg))

    await cosim.init_cosim()
    mem.load_bin(Path(bin_path), load_addr)

    for component in (mem, irq, monitor, scoreboard):
        cocotb.start_soon(component.run())

    return Env(mem=mem, irq=irq, monitor=monitor, scoreboard=scoreboard,
               coverage=coverage)
