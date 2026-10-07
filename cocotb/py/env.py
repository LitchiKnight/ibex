# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the cocotb verification environment.

Only the end-of-test handshake addresses are Python-side configuration. The
co-simulator construction parameters (ISA string, PMP, ICache, debug module
range, ...) live in ``tb/ibex_cocotb_tb.sv``, which is their single
authority, and the image load base address is passed by the Makefile
through the ``+ibex_cocotb_load_addr`` plusarg.

The agent randomisation knobs are runtime plusargs too (``bring_up`` reads
them); the defaults below match the UVM agents.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IbexCocotbConfig:
    # End-of-test handshake addresses. The ibex-patched riscv-test-env writes
    # the result to the signature address (dv/uvm/core_ibex/directed_tests/
    # ibex_macros.h, SIGNATURE_ADDR); a TEST_RESULT write (value[7:0] == 1)
    # with value[8] == 0 means pass. Failures and exceptions also write to the
    # ``.tohost`` section of the directed-test link script, with an odd value.
    # The interpretation itself lives in mem_agent.classify_test_result.
    signature_addr: int = 0x8FFFFFF8
    tohost_addr: int = 0x80001000
    # Memory agent knobs (UVM ibex_mem_intf_response_agent_cfg defaults;
    # overridable through the +ibex_cocotb_spurious_resp and
    # +ibex_cocotb_zero_delays plusargs).
    spurious_response: bool = False
    zero_delays: bool = False
    # IRQ agent knobs (overridable through the +ibex_cocotb_irq and
    # +ibex_cocotb_irq_nmi plusargs).
    irq_enable: bool = False
    irq_nmi_enable: bool = False
    # Poisoned address used by the dedicated error test: accesses to it
    # always receive an error response (unmapped in the co-simulator's
    # memory, so Spike faults on it the same way the DUT does).
    error_addr: int = 0xDEAD0000
