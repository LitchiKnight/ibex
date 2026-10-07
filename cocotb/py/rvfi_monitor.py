# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""RVFI monitor for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/ibex_cosim_agent/ibex_rvfi_monitor.sv``:
one item is emitted per retired instruction, or per IRQ-only event
(``rvfi_ext_irq_valid`` without ``rvfi_valid``). The scoreboard consumes the
items in order and compares each one against the SpikeCosim co-simulator.
"""

import logging
from dataclasses import dataclass

from cocotb.queue import Queue
from cocotb.triggers import RisingEdge

logger = logging.getLogger("cocotb.rvfi_monitor")

# crash_dump_t = {current_pc, next_pc, last_data_addr, exception_pc,
# exception_addr}, MSB first. The single place that knows the layout.
CRASH_DUMP_FIELDS = ("current_pc", "next_pc", "last_data_addr",
                     "exception_pc", "exception_addr")


def decode_crash_dump(dump: int):
    """Decode the tb's latched crash_dump_t into a field-name dict."""
    return {name: (dump >> (32 * (len(CRASH_DUMP_FIELDS) - 1 - i))) & 0xFFFFFFFF
            for i, name in enumerate(CRASH_DUMP_FIELDS)}


@dataclass
class RvfiItem:
    """Fields needed by the scoreboard for one retired instruction or one
    IRQ-only event.

    This is the complete RVFI sampling snapshot: both consumers see the
    same item, so they can never disagree on what was retired. The fields
    ``expanded_insn``/``expanded_valid``/``mode``/``debug_mode`` are used
    by the coverage model only, never by the scoreboard's comparison;
    everything else feeds the comparison (and some of it the coverage
    model as well).
    """

    order: int
    pc: int
    insn: int
    # Expanded encoding of the retired instruction (valid only when
    # expanded_valid is set). rvfi_insn presents unexpanded compressed
    # instructions as {16'b0, 16-bit encoding}; the coverage classifier
    # needs the 32-bit form. The co-simulator keeps using ``insn`` exactly
    # as before.
    expanded_insn: int
    expanded_valid: int
    trap: int
    intr: int
    mode: int
    rd_addr: int
    rd_wdata: int
    rf_wr_suppress: int
    mcycle: int
    pre_mip: int
    post_mip: int
    nmi: int
    nmi_int: int
    debug_req: int
    debug_mode: int
    # IRQ-only events carry no retired instruction; the scoreboard only
    # updates the co-simulator's interrupt state for them.
    irq_only: bool = False
    # The tb's trap crash dump, only meaningful when trap is set.
    crash_dump: int = 0


class RVFIMonitor:
    """Sample the RVFI outputs and queue one item per retirement or IRQ-only
    event.

    ``get()`` returns the items strictly in retirement order and never drops
    one; ``retired_count`` counts only real retirements and is not decreased
    by ``get()``.

    ``on_retire`` is an optional observer called with every retired item
    (never the IRQ-only events) as it is produced; the coverage model is
    its only user today.
    """

    def __init__(self, dut, on_retire=None):
        self.dut = dut
        self.on_retire = on_retire
        self._queue = Queue()
        self.retired_count = 0
        self.trap_count = 0
        self.irq_only_count = 0
        # Cycles where rvfi_ext_irq_valid was high without a retirement
        # (before edge detection). On the WritebackStage=0 tb no interrupt
        # take is exercised, so this stays 0; it documents the dormant
        # irq-only path (see PLAN.md section 9).
        self.raw_irq_cycles = 0
        # (previous_order, jumped_order) of the first RVFI order gap. A gap
        # means a sampling error, which is exactly the kind of bug that
        # otherwise surfaces as baffling cosim mismatches; the tests treat
        # it as fatal rather than letting the run continue.
        self.order_gap = None
        self._prev_order = None
        self._prev_irq_event = False

    async def run(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            valid = int(self.dut.rvfi_valid.value)
            irq_event = int(self.dut.rvfi_ext_irq_valid.value)
            # Consecutive irq-only captures are one interrupt event: the
            # RTL holds rvfi_ext_irq_valid high from the event until the
            # handler's first instruction retires. Edge detection makes
            # this independent of that RTL behaviour; the previous level
            # is tracked every cycle, empty ones included, so a level that
            # drops and rises again is a new event.
            if irq_event and not valid:
                self.raw_irq_cycles += 1
            irq_only = irq_event and not valid and not self._prev_irq_event
            self._prev_irq_event = irq_event
            if not (valid or irq_event):
                continue

            item = RvfiItem(
                order=int(self.dut.rvfi_order.value),
                pc=int(self.dut.rvfi_pc_rdata.value),
                insn=int(self.dut.rvfi_insn.value),
                expanded_insn=int(
                    self.dut.rvfi_ext_expanded_insn.value),
                expanded_valid=int(
                    self.dut.rvfi_ext_expanded_insn_valid.value),
                trap=int(self.dut.rvfi_trap.value),
                intr=int(self.dut.rvfi_intr.value),
                mode=int(self.dut.rvfi_mode.value),
                rd_addr=int(self.dut.rvfi_rd_addr.value),
                rd_wdata=int(self.dut.rvfi_rd_wdata.value),
                rf_wr_suppress=int(self.dut.rvfi_ext_rf_wr_suppress.value),
                mcycle=int(self.dut.rvfi_ext_mcycle.value),
                pre_mip=int(self.dut.rvfi_ext_pre_mip.value),
                post_mip=int(self.dut.rvfi_ext_post_mip.value),
                nmi=int(self.dut.rvfi_ext_nmi.value),
                nmi_int=int(self.dut.rvfi_ext_nmi_int.value),
                debug_req=int(self.dut.rvfi_ext_debug_req.value),
                debug_mode=int(self.dut.rvfi_ext_debug_mode.value),
                irq_only=irq_only,
                crash_dump=int(self.dut.trap_crash_dump.value),
            )

            if item.nmi or item.nmi_int or irq_only:
                logger.debug("item order=%d pc=0x%08x nmi=%d nmi_int=%d "
                             "irq_only=%d", item.order, item.pc, item.nmi,
                             item.nmi_int, int(irq_only))

            # RVFI guarantees one incrementing order value per retirement; a
            # gap would mean a sampling error, which is exactly the kind of
            # bug that otherwise surfaces as baffling cosim mismatches.
            # IRQ-only events retire nothing, so they do not advance order.
            if not irq_only:
                if (self._prev_order is not None
                        and item.order != self._prev_order + 1):
                    logger.error("rvfi order jumped: %d -> %d (missed a "
                                 "retirement?)", self._prev_order, item.order)
                    if self.order_gap is None:
                        self.order_gap = (self._prev_order, item.order)
                self._prev_order = item.order
                self.retired_count += 1
                if item.trap:
                    self.trap_count += 1
            else:
                self.irq_only_count += 1

            if not irq_only and self.on_retire is not None:
                self.on_retire(item)

            self._queue.put_nowait(item)

    async def get(self) -> RvfiItem:
        return await self._queue.get()
