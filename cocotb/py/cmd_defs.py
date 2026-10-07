# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Command-interface layout: the single definition of the opcode numbers,
the packed-argument bit positions and their fingerprint order, shared by
the Python side and the tb (``tb/ibex_cocotb_tb.sv``).

The Python side imports these numbers directly; the tb gets them from
``gen/out/cmd_defs.svh``, which ``gen/cmd_defs.py`` renders from this
table at build time (the fingerprint order included, so the tb walks a
generated array instead of hand-writing the sequence). The CMD_INIT
layout fingerprint remains as a runtime sanity check on top (see
``py/cosim.py``). The tb's command case is the one remaining hand-written
consumer of the opcodes: its ``unique case`` plus the default ``$error``
fail any opcode removed or renamed here.
"""

# Command opcodes.
OPCODES = {
    "CMD_INIT": 0x00,
    "CMD_STEP": 0x01,
    "CMD_GET_ERRORS": 0x02,
    "CMD_GET_INSN_CNT": 0x03,
    "CMD_RELEASE": 0x04,
    "CMD_NOTIFY_DSIDE": 0x05,
    "CMD_SET_MIP": 0x06,
    "CMD_GET_ERROR_STR": 0x07,
    "CMD_SET_ISIDE_ERROR": 0x08,
}

# Bit positions of the packed CMD_STEP arguments.
STEP_BITS = {
    "STEP_RD_ADDR_LSB": 0,
    "STEP_RF_WR_SUPPRESS_BIT": 5,
    "STEP_TRAP_BIT": 6,
    "STEP_NMI_INT_BIT": 7,
    "STEP_NMI_BIT": 8,
    "STEP_DEBUG_REQ_BIT": 9,
}

# Bit positions of the packed CMD_NOTIFY_DSIDE arguments.
DSIDE_BITS = {
    "DSIDE_STORE_BIT": 0,
    "DSIDE_ERROR_BIT": 1,
    "DSIDE_MIS_FIRST_BIT": 2,
    "DSIDE_MIS_SECOND_BIT": 3,
    "DSIDE_MIS_FIRST_ERR_BIT": 4,
    "DSIDE_M_MODE_BIT": 5,
}

# Bit positions of the packed CMD_SET_MIP arguments.
SETMIP_BITS = {
    "SETMIP_NMI_BIT": 1,
    "SETMIP_NMI_INT_BIT": 2,
}


def layout_values():
    """The complete layout in fingerprint order: the opcodes followed by
    the bit positions, exactly matching the tb's generated fingerprint
    function."""
    return [*OPCODES.values(), *STEP_BITS.values(),
            *DSIDE_BITS.values(), *SETMIP_BITS.values()]
