# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Command transport for the SpikeCosim interface in the cocotb environment.

The tb (``tb/ibex_cocotb_tb.sv``) exposes a small request/ack command
interface through which the unchanged dv/cosim DPI functions are called
from SV, because Verilator's VPI does not expose DPI objects to cocotb
directly. ``CosimChannel`` is the only component that talks to that
interface: it owns the opcode numbering, drives the handshake and
serialises all users with a lock. The semantic layers (py/cosim.py and
anything built on it) go through ``cmd()`` and never touch the DUT
signals.
"""

import logging

from cocotb.triggers import Lock, RisingEdge

from cmd_defs import OPCODES

logger = logging.getLogger("cocotb.cosim_channel")

# Upper bound (in clock cycles) for one command handshake. The handshake
# normally takes four cycles; anything beyond this means the interface is
# wedged and waiting longer cannot help.
CMD_WEDGE_CYCLES = 1000


class ChannelError(Exception):
    """The command interface failed (e.g. a wedged handshake)."""


class CosimChannel:
    # Command opcodes from the single layout definition (py/cmd_defs.py);
    # the tb includes the generated copy (gen/out/cmd_defs.svh), so the
    # two sides can never drift. The layout fingerprint CMD_INIT returns
    # remains as a runtime sanity check on top.
    CMD_INIT = OPCODES["CMD_INIT"]
    CMD_STEP = OPCODES["CMD_STEP"]
    CMD_GET_ERRORS = OPCODES["CMD_GET_ERRORS"]
    CMD_GET_INSN_CNT = OPCODES["CMD_GET_INSN_CNT"]
    CMD_RELEASE = OPCODES["CMD_RELEASE"]
    CMD_NOTIFY_DSIDE = OPCODES["CMD_NOTIFY_DSIDE"]
    CMD_SET_MIP = OPCODES["CMD_SET_MIP"]
    CMD_GET_ERROR_STR = OPCODES["CMD_GET_ERROR_STR"]

    def __init__(self, dut):
        self.dut = dut
        # The command interface is shared between the scoreboard steps and
        # the memory agent's access notifications; the Lock serialises them
        # (cocotb's Lock acquires in FIFO order). The interface is restored
        # even when the handshake fails, so the next command cannot wedge.
        self._lock = Lock()

    async def cmd(self, op, a0=0, a1=0, a2=0, a3=0, a4=0, a5=0, a6=0):
        """Drive one command through the tb handshake and return cmd_ret0."""
        async with self._lock:
            try:
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
                    raise ChannelError(
                        "command interface wedged: cmd_ack never asserted")
                ret = int(dut.cmd_ret0.value)

                dut.cmd_valid.value = 0
                await RisingEdge(dut.clk_o)
                for _ in range(CMD_WEDGE_CYCLES):
                    if not int(dut.cmd_ack.value):
                        break
                    await RisingEdge(dut.clk_o)
                else:
                    raise ChannelError(
                        "command interface wedged: cmd_ack never dropped")
                return ret
            finally:
                self.dut.cmd_valid.value = 0
