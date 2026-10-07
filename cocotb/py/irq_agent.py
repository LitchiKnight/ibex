# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Interrupt agent for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/irq_agent``: the five interrupt classes
(software, timer, external, fast[14:0] and the non-maskable interrupt) are
driven as levels for a randomised hold time. Each raise picks a random
combination of lines with the same distribution as the UVM ``irq_seq_item``
(``$countones`` of all lines equals ``num_of_interrupt``, softly one).

The core only samples the lines when the pipeline empties, so holds are kept
generous; the idle gap between raises is randomised the same way.

The agent is a passive stimulus source: whether a raised line actually traps
depends on the program (``mstatus``.MIE, ``mie``). Most vendored riscv-tests
run with MIE cleared, so injection is only observable through the RVFI
``ext_pre_mip``/``ext_post_mip`` fields; the dedicated irq test enables MIE
and takes real traps, exercising the irq-only RVFI path in the scoreboard.
"""

import logging
import random
from dataclasses import dataclass

import cocotb
from cocotb.triggers import RisingEdge

logger = logging.getLogger("cocotb.irq_agent")


@dataclass(frozen=True)
class IrqAgentConfig:
    # Enable the agent at all.
    enable: bool = False
    # Include the non-maskable interrupt in the random line selection. NMI
    # requires a program with an NMI handler at mtvec+0x7c (the dedicated
    # irq test); vendored riscv-tests have none, so it is off by default.
    nmi_enabled: bool = False
    # Cycles between raising lines (idle) and cycles the lines stay raised
    # (hold), chosen uniformly from these ranges.
    idle_cycles_min: int = 20
    idle_cycles_max: int = 200
    hold_cycles_min: int = 20
    hold_cycles_max: int = 200
    # The NMI line is dropped after a short window (one take), before the
    # NMI handler's mret: keeping it high across the mret would re-enter
    # the NMI while the handler's return target is already in flight, and
    # the DUT then retires one extra instruction between the irq-only
    # event and the handler - which the co-simulator's NMI model (a trap
    # at the very next step) cannot match.
    nmi_hold_cycles: int = 15


class IbexIrqAgent:
    """Random interrupt line driver."""

    def __init__(self, dut, cfg: IrqAgentConfig):
        self.dut = dut
        self.cfg = cfg
        self.raise_count = 0

    def _pick_lines(self):
        """Choose the raised line combination. UVM-aligned: the number of
        raised lines is uniform over all combinations with a soft preference
        for exactly one; when the NMI is disabled it is never raised."""
        lines = ["irq_software", "irq_timer", "irq_external",
                 *[f"irq_fast[{i}]" for i in range(15)]]
        if self.cfg.nmi_enabled:
            lines.append("irq_nm")
        num = random.choices(range(len(lines)),
                             weights=[1] + [4] + [2] * (len(lines) - 2))[0]
        return random.sample(lines, num) if num else []

    async def _drive(self, names, value):
        for name in names:
            if name.startswith("irq_fast"):
                idx = int(name.split("[")[1].rstrip("]"))
                self.dut.irq_fast_i[idx].value = value
            else:
                getattr(self.dut, f"{name}_i").value = value
        await RisingEdge(self.dut.clk_o)

    async def run(self):
        if not self.cfg.enable:
            logger.info("irq agent: disabled")
            return
        logger.info("irq agent: raising random interrupts "
                    "(nmi=%s)", self.cfg.nmi_enabled)
        while True:
            # Idle gap.
            await RisingEdge(self.dut.clk_o)
            for _ in range(random.randint(self.cfg.idle_cycles_min,
                                          self.cfg.idle_cycles_max)):
                await RisingEdge(self.dut.clk_o)

            names = self._pick_lines()
            if names:
                self.raise_count += 1
                await self._drive(names, 1)
                # Hold long enough for the core to drain the pipeline and
                # take the interrupt. The NMI is dropped after a short
                # window so it is taken exactly once (see the config
                # comment); the maskable lines may stay high across their
                # handler's mret and simply re-trap.
                nmi_names = [n for n in names if n == "irq_nm"]
                if nmi_names:
                    for _ in range(self.cfg.nmi_hold_cycles):
                        await RisingEdge(self.dut.clk_o)
                    await self._drive(nmi_names, 0)
                for _ in range(random.randint(self.cfg.hold_cycles_min,
                                              self.cfg.hold_cycles_max)):
                    await RisingEdge(self.dut.clk_o)
                await self._drive(names, 0)
