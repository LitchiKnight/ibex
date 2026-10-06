# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Scoreboard for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/ibex_cosim_agent/ibex_cosim_scoreboard.sv``:
each retired instruction captured by the RVFI monitor is stepped through the
SpikeCosim co-simulator (the unchanged C++ comparison core from dv/cosim,
see cocotb/PLAN.md) and the results are compared. The DPI calls themselves
are issued from ``tb/ibex_cocotb_tb.sv`` through a small command interface,
because Verilator's VPI does not expose DPI objects to cocotb directly.
"""

import logging
from collections import deque

from cocotb.triggers import Event, RisingEdge

from mem_agent import DsideAccess

logger = logging.getLogger("cocotb.scoreboard")

# Upper bound (in clock cycles) for one command handshake. The handshake
# normally takes four cycles; anything beyond this means the interface is
# wedged and waiting longer cannot help.
CMD_WEDGE_CYCLES = 1000


class ScoreboardError(Exception):
    pass


class Scoreboard:
    # Command opcodes (keep in sync with tb/ibex_cocotb_tb.sv).
    CMD_INIT = 0
    CMD_STEP = 1
    CMD_GET_ERRORS = 2
    CMD_GET_INSN_CNT = 3
    CMD_RELEASE = 4
    CMD_NOTIFY_DSIDE = 5

    def __init__(self, dut):
        self.dut = dut
        self.processed = 0
        self.error = None
        self.done_event = Event()
        self._cmd_queue = deque()

    async def cmd(self, op, a0=0, a1=0, a2=0, a3=0, a4=0, a5=0, a6=0):
        # The command interface is shared between the scoreboard and the
        # memory agent's access notifications, so commands are serialized
        # with a FIFO queue: a waiter may proceed only when it is the head.
        # The head check has no await in it, so it is atomic under cocotb's
        # single-threaded scheduler.
        token = object()
        self._cmd_queue.append(token)
        try:
            while self._cmd_queue[0] is not token:
                await RisingEdge(self.dut.clk_o)

            dut = self.dut
            dut.cmd_op.value = op
            dut.cmd_a0.value = a0
            dut.cmd_a1.value = a1
            dut.cmd_a2.value = a2
            dut.cmd_a3.value = a3
            dut.cmd_a4.value = a4
            dut.cmd_a5.value = a5
            dut.cmd_a6.value = a6
            dut.cmd_valid.value = 1

            await RisingEdge(dut.clk_o)
            for _ in range(CMD_WEDGE_CYCLES):
                if int(dut.cmd_ack.value):
                    break
                await RisingEdge(dut.clk_o)
            else:
                raise ScoreboardError(
                    "command interface wedged: cmd_ack never asserted")
            ret = int(dut.cmd_ret0.value)

            dut.cmd_valid.value = 0
            await RisingEdge(dut.clk_o)
            for _ in range(CMD_WEDGE_CYCLES):
                if not int(dut.cmd_ack.value):
                    break
                await RisingEdge(dut.clk_o)
            else:
                raise ScoreboardError(
                    "command interface wedged: cmd_ack never dropped")
            return ret
        finally:
            # Restore the interface even if the transaction failed, so the
            # queue cannot wedge permanently.
            assert self._cmd_queue[0] is token
            self._cmd_queue.popleft()
            self.dut.cmd_valid.value = 0

    async def init_cosim(self):
        if not await self.cmd(self.CMD_INIT):
            raise ScoreboardError("spike_cosim_init failed (see sim log)")
        logger.info("SpikeCosim initialised")

    async def run(self, monitor):
        try:
            while True:
                item = await monitor.get()
                await self.step_item(item)
                self.processed += 1
        except ScoreboardError as error:
            self.error = str(error)
            logger.error("scoreboard stopped: %s", error)
        finally:
            self.done_event.set()

    async def step_item(self, item):
        # CMD_STEP argument packing. This is the authoritative definition of
        # the bit layout; tb/ibex_cocotb_tb.sv only decodes it through named
        # constants (STEP_*). The tb issues the DPI calls in the same order
        # as the UVM scoreboard: debug_req, nmi, nmi_int, mip, mcycle, step.
        #   cmd_a0 : pc, cmd_a1 : rd_wdata,
        #   cmd_a2 : {22'b0, debug_req, nmi, nmi_int, trap,
        #             rf_wr_suppress, rd_addr[4:0]},
        #   cmd_a3/a4 : pre_mip/post_mip, cmd_a5/a6 : mcycle[31:0]/[63:32].
        a2 = (
            (item.rd_addr & 0x1F)
            | ((item.rf_wr_suppress & 1) << 5)
            | ((item.trap & 1) << 6)
            | ((item.nmi_int & 1) << 7)
            | ((item.nmi & 1) << 8)
            | ((item.debug_req & 1) << 9)
        )
        ok = await self.cmd(
            self.CMD_STEP,
            a0=item.pc,
            a1=item.rd_wdata,
            a2=a2,
            a3=item.pre_mip,
            a4=item.post_mip,
            a5=item.mcycle & 0xFFFFFFFF,
            a6=(item.mcycle >> 32) & 0xFFFFFFFF,
        )
        if not ok:
            num_errors = await self.cmd(self.CMD_GET_ERRORS)
            raise ScoreboardError(
                "cosim mismatch at order={} pc=0x{:08x} (insn=0x{:08x}, "
                "{} error(s), see sim log)".format(
                    item.order, item.pc, item.insn, num_errors)
            )

    async def notify_dside(self, access: DsideAccess):
        """Tell the co-simulator about a data-side access seen on the memory
        interface, once its response has been observed (mirrors
        ``riscv_cosim_notify_dside_access`` in the UVM scoreboard).

        M1 note: the rv32ui tests run in M mode throughout and use aligned
        accesses, so ``m_mode_access`` is fixed and the misaligned flags are
        always clear; randomized access modelling arrives with M2.
        """
        # CMD_NOTIFY_DSIDE argument packing (authoritative here; the tb
        # decodes it with the DSIDE_* constants):
        #   cmd_a0 : addr, cmd_a1 : data, cmd_a2 : byte enables,
        #   cmd_a3 : {26'b0, m_mode_access, misaligned_first_saw_error,
        #             misaligned_second, misaligned_first, error, store}
        a3 = ((1 if access.store else 0)
              | ((1 if access.error else 0) << 1)
              | ((1 if access.misaligned_first else 0) << 2)
              | ((1 if access.misaligned_second else 0) << 3)
              | ((1 if access.misaligned_first_saw_error else 0) << 4)
              | ((1 if access.m_mode_access else 0) << 5))
        await self.cmd(self.CMD_NOTIFY_DSIDE, a0=access.addr, a1=access.data,
                       a2=access.be, a3=a3)

    async def finish(self) -> int:
        insn_cnt = await self.cmd(self.CMD_GET_INSN_CNT)
        await self.cmd(self.CMD_RELEASE)
        logger.info("SpikeCosim released after %d matched instructions",
                    insn_cnt)
        return insn_cnt
