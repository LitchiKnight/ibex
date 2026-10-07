# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Dedicated instruction-fetch-error test for the cocotb verification
# environment.
#
# Self-contained (does not use the vendored riscv-test-env). The memory
# agent answers every fetch from ERROR_ADDR with a bus error, and the
# co-simulator's sparse memory leaves that address unmapped, so both the
# DUT and Spike take an instruction access fault. The test jumps to
# ERROR_ADDR, expects the trap with mcause == CAUSE_FETCH_ACCESS_FAULT
# and mepc == the faulting address, redirects mepc back into the test in
# the handler, and checks afterwards that the trap was actually taken.
#
# ERROR_ADDR has one definition: the Makefile's IBEX_ERROR_ADDR variable
# (shared with the data-side error test), passed to this file as
# -DERROR_ADDR and to the memory agent through the
# +ibex_cocotb_error_addrs plusarg (see py/tb_env.py). The fallback below
# only keeps a standalone build working.
#
# The irq agent is off for this test (no interrupts at all), so the trap
# handler does not need to be re-entrant.
#
# Result handshake, same as the vendored tests: a write of 1 to the
# signature address (0x8ffffff8) on pass, an odd write to the tohost
# section (0x80001000) on fail (see mem_agent.classify_test_result).

#include "common.inc"

#ifndef ERROR_ADDR
#define ERROR_ADDR     0xdead0000
#endif
#define CAUSE_FETCH_ACCESS_FAULT 1

  DIRECTED_TEST_ENTRY

  .org 0x100
trap_handler:
  # Only an instruction access fault on the poisoned fetch is expected.
  csrr t0, mcause
  li t1, CAUSE_FETCH_ACCESS_FAULT
  bne t0, t1, handler_fail
  csrr t0, mepc
  li t1, ERROR_ADDR
  bne t0, t1, handler_fail

  # Record that the trap was taken and resume in the test code: the
  # poisoned address cannot be skipped past (its neighbours are poisoned
  # too), so mepc is redirected to fetch_resume.
  la t2, fault_seen
  li t0, 1
  sw t0, 0(t2)
  la t0, fetch_resume
  csrw mepc, t0
  mret

handler_fail:
  li t0, TOHOST_ADDR
  slli t1, t1, 1
  ori t1, t1, 1
  sw t1, 0(t0)
1:
  j 1b

  .org 0x200
reset_vector:
  DIRECTED_TEST_INIT

  la s3, fault_seen
  sw x0, 0(s3)

fault_fetch:
  # Jump to the poisoned address; the fetch traps and the handler resumes
  # at fetch_resume.
  li t0, ERROR_ADDR
  jalr x0, t0, 0
fetch_resume:

  # Back from the trap: it must have been taken.
  la s2, fault_seen
  lw s2, 0(s2)
  beqz s2, fail

  # Pass: TEST_RESULT (1) at the signature address.
  li s0, 1
  li s1, SIGNATURE_ADDR
  sw s0, 0(s1)
done:
  j done

fail:
  # 3, not 1: the environment classifies a tohost write of 1 as pass (the
  # vendored ecall/scall convention), so a dedicated test's generic
  # failure must be a different odd value.
  li s0, TOHOST_ADDR
  li s1, 3
  sw s1, 0(s0)
  j done

  DIRECTED_TEST_TOHOST_ANCHOR

  .bss
  .balign 4
fault_seen:
  .word 0
