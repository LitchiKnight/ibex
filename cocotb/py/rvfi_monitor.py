# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""RVFI monitor for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/ibex_cosim_agent/ibex_rvfi_monitor.sv``:
one item is emitted per retired instruction (or IRQ-only event). The
scoreboard consumes the items in order and compares each one against the
SpikeCosim co-simulator.
"""

import logging
from dataclasses import dataclass

from cocotb.queue import Queue
from cocotb.triggers import RisingEdge

logger = logging.getLogger("cocotb.rvfi_monitor")


@dataclass
class RvfiItem:
    """Fields needed by the scoreboard for one retired instruction."""

    order: int
    pc: int
    insn: int
    trap: int
    rd_addr: int
    rd_wdata: int
    rf_wr_suppress: int
    mcycle: int
    pre_mip: int
    post_mip: int
    nmi: int
    nmi_int: int
    debug_req: int


class RVFIMonitor:
    """Sample the RVFI outputs and queue one item per ``rvfi_valid`` cycle.

    ``get()`` returns the items strictly in retirement order and never drops
    one; ``retired_count`` counts everything captured so far and is not
    decreased by ``get()``.
    """

    def __init__(self, dut):
        self.dut = dut
        self._queue = Queue()
        self.retired_count = 0
        self._prev_order = None

    async def run(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            if not int(self.dut.rvfi_valid.value):
                continue

            item = RvfiItem(
                order=int(self.dut.rvfi_order.value),
                pc=int(self.dut.rvfi_pc_rdata.value),
                insn=int(self.dut.rvfi_insn.value),
                trap=int(self.dut.rvfi_trap.value),
                rd_addr=int(self.dut.rvfi_rd_addr.value),
                rd_wdata=int(self.dut.rvfi_rd_wdata.value),
                rf_wr_suppress=int(self.dut.rvfi_ext_rf_wr_suppress.value),
                mcycle=int(self.dut.rvfi_ext_mcycle.value),
                pre_mip=int(self.dut.rvfi_ext_pre_mip.value),
                post_mip=int(self.dut.rvfi_ext_post_mip.value),
                nmi=int(self.dut.rvfi_ext_nmi.value),
                nmi_int=int(self.dut.rvfi_ext_nmi_int.value),
                debug_req=int(self.dut.rvfi_ext_debug_req.value),
            )

            # RVFI guarantees one incrementing order value per retirement; a
            # gap would mean a sampling error, which is exactly the kind of
            # bug that otherwise surfaces as baffling cosim mismatches.
            if self._prev_order is not None and item.order != self._prev_order + 1:
                logger.error("rvfi order jumped: %d -> %d (missed a "
                             "retirement?)", self._prev_order, item.order)
            self._prev_order = item.order

            self._queue.put_nowait(item)
            self.retired_count += 1

    async def get(self) -> RvfiItem:
        return await self._queue.get()
