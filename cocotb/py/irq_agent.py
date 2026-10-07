# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Interrupt agent for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/irq_agent``: the five interrupt classes
(software, timer, external, fast[14:0] and the non-maskable interrupt) are
driven as levels for a randomised hold time. Each raise picks a random
combination of lines with the same distribution as the UVM ``irq_seq_item``
(``$countones`` of all lines equals ``num_of_interrupt``, softly one).

The agent owns its lines: it drives them all low at construction, so no
other component needs to know the line list. The core only samples the
lines when the pipeline empties, so holds are kept generous; the idle gap
between raises is randomised the same way.

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
class IrqLine:
    """One interrupt input: the tb port name, plus the index for fast
    lines."""

    name: str
    fast_index: int | None = None

    @property
    def is_nmi(self) -> bool:
        return self.name == "irq_nm"


# The full line list; the tb ports are derived from it through _signal(),
# the single place that knows the <name>_i port-name convention.
LINES = (IrqLine("irq_software"), IrqLine("irq_timer"), IrqLine("irq_external"),
         *(IrqLine(f"irq_fast[{i}]", i) for i in range(15)),
         IrqLine("irq_nm"))


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


# UVM irq_seq_item: $countones(all lines) == num_of_interrupt with a soft
# preference for exactly one. The weights are per count (0 / 1 / >=2), not
# per combination.
_NONE_WEIGHT = 1
_ONE_WEIGHT = 4
_MANY_WEIGHT = 2


def _count_weights(num_lines: int):
    return [_NONE_WEIGHT, _ONE_WEIGHT] + [_MANY_WEIGHT] * (num_lines - 2)


class IbexIrqAgent:
    """Random interrupt line driver."""

    def __init__(self, dut, cfg: IrqAgentConfig):
        self.dut = dut
        self.cfg = cfg
        self.raise_count = 0
        # The agent owns its lines: drive them low before anything else can
        # observe them.
        for line in LINES:
            self._signal(line).value = 0

    def _signal(self, line: IrqLine):
        """The tb port for a line; the single place that knows the
        ``<name>_i`` port-name convention."""
        if line.fast_index is not None:
            return self.dut.irq_fast_i[line.fast_index]
        return getattr(self.dut, f"{line.name}_i")

    def _pick_lines(self):
        """Choose the raised line combination, UVM-aligned (see the weight
        comment above); when the NMI is disabled it is never raised."""
        lines = [line for line in LINES
                 if not line.is_nmi or self.cfg.nmi_enabled]
        num = random.choices(range(len(lines)),
                             weights=_count_weights(len(lines)))[0]
        return random.sample(lines, num) if num else []

    async def _wait(self, cycles):
        for _ in range(cycles):
            await RisingEdge(self.dut.clk_o)

    async def _drive(self, lines, value):
        for line in lines:
            self._signal(line).value = value
        await RisingEdge(self.dut.clk_o)

    async def _raise_and_drop(self, lines):
        """Raise the lines, hold them for a random window, then drop. The
        NMI is dropped after a short window so it is taken exactly once
        (see the config comment); the maskable lines may stay high across
        their handler's mret and simply re-trap."""
        await self._drive(lines, 1)
        nmi_lines = [line for line in lines if line.is_nmi]
        if nmi_lines:
            await self._wait(self.cfg.nmi_hold_cycles)
            await self._drive(nmi_lines, 0)
        await self._wait(random.randint(self.cfg.hold_cycles_min,
                                        self.cfg.hold_cycles_max))
        await self._drive(lines, 0)

    async def run(self):
        if not self.cfg.enable:
            logger.info("irq agent: disabled")
            return
        logger.info("irq agent: raising random interrupts "
                    "(nmi=%s)", self.cfg.nmi_enabled)
        while True:
            await self._wait(random.randint(self.cfg.idle_cycles_min,
                                            self.cfg.idle_cycles_max))
            lines = self._pick_lines()
            if lines:
                self.raise_count += 1
                await self._raise_and_drop(lines)
