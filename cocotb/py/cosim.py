# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Semantic layer over the tb's SpikeCosim command interface.

``Cosim`` maps the raw command channel (``cosim_channel.CosimChannel``) to
the co-simulator operations the environment needs: initialisation, one
retirement step, IRQ-only MIP updates, data-side access notifications,
error reporting and release. This file is the single authority for the
packed command arguments; ``tb/ibex_cocotb_tb.sv`` only decodes them
through its named constants, and the tb issues the underlying DPI calls in
the same order as the UVM cosim scoreboard.

The comparison core itself (dv/cosim) is compiled into the simulation
unchanged; nothing here reimplements any checking.
"""

import logging
from typing import TYPE_CHECKING

from cosim_channel import CosimChannel

if TYPE_CHECKING:
    from mem_agent import DsideAccess

logger = logging.getLogger("cocotb.cosim")


class CosimError(Exception):
    """A co-simulator operation failed (init, transport, mismatch)."""


class CosimMismatchError(CosimError):
    """A retired instruction compared unequal to the co-simulator.

    Structured so tests and logs can consume the fields directly instead of
    parsing a formatted message.
    """

    def __init__(self, order, pc, insn, trap, errors, crash_dump):
        self.order = order
        self.pc = pc
        self.insn = insn
        self.trap = trap
        # Error strings drained from the co-simulator.
        self.errors = errors
        # Decoded trap crash dump (see rvfi_monitor.decode_crash_dump), or
        # None when the mismatching instruction did not trap.
        self.crash_dump = crash_dump
        details = "; ".join(errors) if errors else "no error details"
        message = ("cosim mismatch at order={} pc=0x{:08x} (insn=0x{:08x}, "
                   "trap={}): {}").format(order, pc, insn, trap, details)
        if crash_dump:
            message += (" (exception_pc=0x{:08x}, exception_addr=0x{:08x})"
                        ).format(crash_dump["exception_pc"],
                                 crash_dump["exception_addr"])
        super().__init__(message)


class Cosim:
    """Typed operations on the co-simulator, backed by a CosimChannel."""

    def __init__(self, channel: CosimChannel):
        self._ch = channel

    async def init_cosim(self):
        if not await self._ch.cmd(self._ch.CMD_INIT):
            raise CosimError("spike_cosim_init failed (see sim log)")
        logger.info("SpikeCosim initialised")

    async def step(self, *, pc, rd_wdata, rd_addr, rf_wr_suppress, trap,
                   nmi_int, nmi, debug_req, pre_mip, post_mip,
                   mcycle) -> bool:
        """Step the co-simulator with one retired instruction; False means
        the comparison failed (the error strings are then available through
        ``error_str()``/``drain_errors()``)."""
        # CMD_STEP argument packing. This is the authoritative definition of
        # the bit layout; tb/ibex_cocotb_tb.sv only decodes it through named
        # constants (STEP_*). The tb issues the DPI calls in the same order
        # as the UVM scoreboard: debug_req, nmi, nmi_int, mip, mcycle, step.
        #   cmd_a0 : pc, cmd_a1 : rd_wdata,
        #   cmd_a2 : {22'b0, debug_req, nmi, nmi_int, trap,
        #             rf_wr_suppress, rd_addr[4:0]},
        #   cmd_a3/a4 : pre_mip/post_mip, cmd_a5/a6 : mcycle[31:0]/[63:32].
        a2 = (
            (rd_addr & 0x1F)
            | ((rf_wr_suppress & 1) << 5)
            | ((trap & 1) << 6)
            | ((nmi_int & 1) << 7)
            | ((nmi & 1) << 8)
            | ((debug_req & 1) << 9)
        )
        ok = await self._ch.cmd(
            self._ch.CMD_STEP,
            a0=pc,
            a1=rd_wdata,
            a2=a2,
            a3=pre_mip,
            a4=post_mip,
            a5=mcycle & 0xFFFFFFFF,
            a6=(mcycle >> 32) & 0xFFFFFFFF,
        )
        return bool(ok)

    async def set_mip(self, nmi, nmi_int, pre_mip):
        """Tell the co-simulator about an IRQ-only RVFI event: set_nmi,
        set_nmi_int, set_mip(pre_mip, pre_mip), no step (the UVM
        scoreboard's irq_only branch)."""
        # cmd_a2 : {29'b0, nmi_int, nmi, 1'b0}; cmd_a3 is used for both mip
        # arguments, as in UVM.
        a2 = ((nmi & 1) << 1) | ((nmi_int & 1) << 2)
        await self._ch.cmd(self._ch.CMD_SET_MIP, a2=a2, a3=pre_mip)

    async def notify_dside(self, access: "DsideAccess"):
        """Tell the co-simulator about a data-side access seen on the memory
        interface, once its response has been observed (mirrors
        ``riscv_cosim_notify_dside_access`` in the UVM scoreboard).

        The error/misaligned/m_mode flags come from the LSU probes the tb
        latches at the (request && grant) address phase; the memory agent
        carries them in the DsideAccess.
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
        await self._ch.cmd(self._ch.CMD_NOTIFY_DSIDE, a0=access.addr,
                           a1=access.data, a2=access.be, a3=a3)

    async def error_str(self, index: int) -> str:
        """The indexed comparison error string (no side effects); an
        out-of-range index returns an empty string. The string is
        transferred 32 bits at a time (a1 == -1 asks for its length, word
        w carries bytes [4w..4w+3]); the channel lock serialises each
        transfer against every other command user."""
        length = await self._ch.cmd(self._ch.CMD_GET_ERROR_STR, a0=index,
                                    a1=0xFFFFFFFF)
        if length <= 0:
            return ""
        words = [await self._ch.cmd(self._ch.CMD_GET_ERROR_STR, a0=index,
                                    a1=word)
                 for word in range((length + 3) // 4)]
        data = b"".join(word.to_bytes(4, "little") for word in words)
        return data[:length].decode("utf-8", "replace")

    async def drain_errors(self) -> int:
        """Print and clear the accumulated comparison errors, returning the
        number of errors reported."""
        return await self._ch.cmd(self._ch.CMD_GET_ERRORS)

    async def get_insn_cnt(self) -> int:
        return await self._ch.cmd(self._ch.CMD_GET_INSN_CNT)

    async def release(self):
        await self._ch.cmd(self._ch.CMD_RELEASE)
