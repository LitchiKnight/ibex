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


@dataclass
class RvfiItem:
    """Fields needed by the scoreboard for one retired instruction or one
    IRQ-only event."""

    order: int
    pc: int
    insn: int
    trap: int
    intr: int
    rd_addr: int
    rd_wdata: int
    rf_wr_suppress: int
    mcycle: int
    pre_mip: int
    post_mip: int
    nmi: int
    nmi_int: int
    debug_req: int
    # IRQ-only events carry no retired instruction; the scoreboard only
    # updates the co-simulator's interrupt state for them.
    irq_only: bool = False


class RVFIMonitor:
    """Sample the RVFI outputs and queue one item per retirement or IRQ-only
    event.

    ``get()`` returns the items strictly in retirement order and never drops
    one; ``retired_count`` counts only real retirements and is not decreased
    by ``get()``.
    """

    def __init__(self, dut):
        self.dut = dut
        self._queue = Queue()
        self.retired_count = 0
        self.irq_only_count = 0
        self._prev_order = None
        self._prev_irq_only = False

    async def run(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            valid = int(self.dut.rvfi_valid.value)
            irq_event = int(self.dut.rvfi_ext_irq_valid.value)
            if not (valid or irq_event):
                continue

            # Consecutive irq-only captures are one interrupt event: the
            # RTL holds rvfi_ext_irq_valid high from the event until the
            # handler's first instruction retires.
            irq_only = (irq_event and not valid) and not self._prev_irq_only
            item = RvfiItem(
                order=int(self.dut.rvfi_order.value),
                pc=int(self.dut.rvfi_pc_rdata.value),
                insn=int(self.dut.rvfi_insn.value),
                trap=int(self.dut.rvfi_trap.value),
                intr=int(self.dut.rvfi_intr.value),
                rd_addr=int(self.dut.rvfi_rd_addr.value),
                rd_wdata=int(self.dut.rvfi_rd_wdata.value),
                rf_wr_suppress=int(self.dut.rvfi_ext_rf_wr_suppress.value),
                mcycle=int(self.dut.rvfi_ext_mcycle.value),
                pre_mip=int(self.dut.rvfi_ext_pre_mip.value),
                post_mip=int(self.dut.rvfi_ext_post_mip.value),
                nmi=int(self.dut.rvfi_ext_nmi.value),
                nmi_int=int(self.dut.rvfi_ext_nmi_int.value),
                debug_req=int(self.dut.rvfi_ext_debug_req.value),
                irq_only=irq_only,
            )

            if item.trap:
                # crash_dump_t = {current_pc, next_pc, last_data_addr,
                # exception_pc, exception_addr}, MSB first.
                dump = int(self.dut.trap_crash_dump.value)
                logger.debug(
                    "trap at order=%d pc=0x%08x: exception_pc=0x%08x "
                    "exception_addr=0x%08x next_pc=0x%08x last_data_addr="
                    "0x%08x",
                    item.order, item.pc, (dump >> 32) & 0xFFFFFFFF,
                    dump & 0xFFFFFFFF, (dump >> 96) & 0xFFFFFFFF,
                    (dump >> 64) & 0xFFFFFFFF)

            # RVFI guarantees one incrementing order value per retirement; a
            # gap would mean a sampling error, which is exactly the kind of
            # bug that otherwise surfaces as baffling cosim mismatches.
            # IRQ-only events retire nothing, so they do not advance order.
            if item.nmi or item.nmi_int or irq_only:
                logger.debug("item order=%d pc=0x%08x nmi=%d nmi_int=%d "
                             "irq_only=%d", item.order, item.pc, item.nmi,
                             item.nmi_int, int(irq_only))
            self._prev_irq_only = irq_only
            if not irq_only:
                if (self._prev_order is not None
                        and item.order != self._prev_order + 1):
                    logger.error("rvfi order jumped: %d -> %d (missed a "
                                 "retirement?)", self._prev_order, item.order)
                self._prev_order = item.order
                self.retired_count += 1
            else:
                self.irq_only_count += 1

            self._queue.put_nowait(item)

    async def get(self) -> RvfiItem:
        return await self._queue.get()
