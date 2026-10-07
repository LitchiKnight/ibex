# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Configuration for the cocotb verification environment.

The single authority for the Python-side configuration: each test declares
the knobs it needs with ``dataclasses.replace`` on this object (see
tests/test_core.py). The plusargs are only an override mechanism applied in
one place in ``tb_env.bring_up``; when a plusarg is given it wins.

The co-simulator construction parameters (ISA string, PMP, ICache, debug
module range, ...) live in ``tb/ibex_cocotb_tb.sv``, which is their single
authority, and the image load base address is passed by the Makefile
through the ``+ibex_cocotb_load_addr`` plusarg.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IbexCocotbConfig:
    # End-of-test handshake addresses. The ibex-patched riscv-test-env writes
    # the result to the signature address (dv/uvm/core_ibex/directed_tests/
    # ibex_macros.h, SIGNATURE_ADDR); a TEST_RESULT write (value[7:0] == 1)
    # with value[8] == 0 means pass. The ecall tests pass through the trap
    # vector instead, writing TESTNUM == 1 to the ``.tohost`` section of the
    # directed-test link script. The interpretation itself lives in
    # mem_agent.TestHandshake.classify.
    signature_addr: int = 0x8FFFFFF8
    tohost_addr: int = 0x80001000
    # Agent knobs, declared per test (the plusargs may override them).
    spurious_response: bool = False
    irq_enable: bool = False
    irq_nmi_enable: bool = False
    # Functional coverage model (py/coverage.py): sampled through the
    # monitor and memory agent callbacks, reported by test_lib at the end
    # of the run. Off only saves the per-item Python calls.
    coverage_enable: bool = True
    # Poisoned addresses for the memory agent: accesses to them always
    # receive an error response (unmapped in the co-simulator's memory, so
    # Spike faults on them the same way the DUT does).
    error_addrs: tuple = ()
