# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Dedicated error-injection test for the cocotb verification environment.
#
# Self-contained (does not use the vendored riscv-test-env). Layout of
# .text.init (linked at 0x80000000 by dv/uvm/core_ibex/directed_tests/
# link.ld):
#   0x80   _start
#   0x100  trap handler (mtvec base, set by reset_vector)
#   0x200  reset_vector
#
# The memory agent answers every access to ERROR_ADDR with a bus error,
# and the co-simulator's sparse memory leaves that address unmapped, so
# both the DUT and Spike take a load access fault. The test loads from
# ERROR_ADDR, expects the trap with mcause == CAUSE_LOAD_ACCESS_FAULT and
# mepc == the faulting lw, skips the faulting instruction in the handler,
# and checks afterwards that the trap was actually taken and that the
# faulted load did not write its register.
#
# ERROR_ADDR has one definition: the Makefile's IBEX_ERROR_ADDR variable,
# passed to this file as -DERROR_ADDR and to the memory agent through the
# +ibex_cocotb_error_addrs plusarg (see py/tb_env.py). The fallback below
# only keeps a standalone build working.
#
# The irq agent is off for this test (no interrupts at all), so the trap
# handler does not need to be re-entrant.
#
# Result handshake, same as the vendored tests: a write of 1 to the
# signature address (0x8ffffff8) on pass, an odd write to the tohost
# section (0x80001000) on fail (see mem_agent.classify_test_result).

#define SIGNATURE_ADDR 0x8ffffff8
#define TOHOST_ADDR    0x80001000
#ifndef ERROR_ADDR
#define ERROR_ADDR     0xdead0000
#endif
#define CAUSE_LOAD_ACCESS_FAULT 5

  .section .text.init
  .option norvc

  .globl _start
  .org 0x80
_start:
  j reset_vector

  .org 0x100
trap_handler:
  # Only a load access fault on fault_lw is expected.
  csrr t0, mcause
  li t1, CAUSE_LOAD_ACCESS_FAULT
  bne t0, t1, handler_fail
  csrr t0, mepc
  la t1, fault_lw
  bne t0, t1, handler_fail

  # Record that the trap was taken and skip the faulting instruction.
  la t2, fault_seen
  li t0, 1
  sw t0, 0(t2)
  csrr t0, mepc
  addi t0, t0, 4
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
  # PMP: permit all accesses (mirrors INIT_PMP in the vendored env).
  li s0, -1
  csrw pmpaddr0, s0
  li s1, 0x1f                     # NAPOT | R | W | X
  csrw pmpcfg0, s1

  la s2, trap_handler
  csrw mtvec, s2

  la s3, fault_seen
  sw x0, 0(s3)
  li s0, ERROR_ADDR               # expected post-fault value of s1
  li s1, ERROR_ADDR
fault_lw:
  lw s1, 0(s1)

  # Back from the trap: it must have been taken...
  la s2, fault_seen
  lw s2, 0(s2)
  beqz s2, fail
  # ...and the faulted load must not have written its register (s1 must
  # still hold the address it held before the lw).
  bne s1, s0, fail

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

  # Anchor the .tohost section (the directed-test link script leaves it
  # empty otherwise, which pulls .bss into the 0x80001000 slot that the
  # environment watches as the tohost handshake address).
  .pushsection .tohost, "aw", @progbits
  .align 6
  .global tohost
tohost:
  .dword 0
  .popsection

  .bss
  .balign 4
fault_seen:
  .word 0
