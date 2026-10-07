# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Dedicated interrupt test for the cocotb verification environment.
#
# Self-contained (does not use the vendored riscv-test-env) so that the NMI
# vector can be placed at mtvec+0x7c: with the vendored env's layout that
# address falls inside the reset_vector code. Layout of .text.init (linked
# at 0x80000000 by dv/uvm/core_ibex/directed_tests/link.ld):
#   0x80   _start
#   0x100  direct trap handler (mtvec base, set by reset_vector)
#   0x17c  NMI handler (the core enters at mtvec + 0x7c)
#   0x200  reset_vector
#
# Register convention (this file is its own interruptible world, so it
# reserves scratch registers per handler and keeps all live state in saved
# registers - no handler needs to save or restore anything, which removes
# the usual mscratch race between a maskable handler and an NMI nested
# inside it):
#   t0-t2  reserved for the NMI handler
#   t3-t6  reserved for the maskable handler
#   s0-s11 all live state of interruptible code
#
# The test enables mstatus.MIE and computes a rolling checksum over an
# array while the irq agent raises random maskable interrupts and NMIs.
# Ibex's internal mstack CSRs restore mepc/mstatus/mcause on the mret that
# leaves NMI mode, so neither handler touches them. Both handlers count
# their entries.
#
# At the end the checksum and both counters are checked: the test passes
# only if the interrupted computation is intact and interrupts were
# actually taken. The result uses the same handshake as the vendored
# tests: a write of 1 to the signature address (0x8ffffff8) on pass, an
# odd write to the tohost section (0x80001000) on fail (see
# mem_agent.classify_test_result).

#define SIGNATURE_ADDR 0x8ffffff8
#define TOHOST_ADDR    0x80001000
#define MSTATUS_MIE    0x8

  .section .text.init
  .option norvc

  .globl _start
  .org 0x80
_start:
  j reset_vector

  .org 0x100
trap_handler:
  # Maskable interrupts and exceptions, direct mode (mtvec base address).
  # Uses only t3-t6, which the NMI handler never touches, so an NMI nested
  # inside this handler is harmless.
  csrr t3, mcause
  bltz t3, 1f                    # interrupt: count it
  j handler_fail                 # exception: unexpected, fail
1:
  la t4, irq_count
  lw t5, 0(t4)
  addi t5, t5, 1
  sw t5, 0(t4)
  mret

  .org 0x17c
nmi_handler:
  # NMI. Uses only t0-t2, which the maskable handler never touches. The
  # hardware mstack CSRs restore mepc/mstatus/mcause on the mret that
  # leaves NMI mode; this handler must not touch them.
  la t0, nmi_count
  lw t1, 0(t0)
  addi t1, t1, 1
  sw t1, 0(t0)
  mret

  .org 0x200
reset_vector:
  # Everything before MIE is enabled still runs with NMIs possible, so all
  # live state uses s-registers only.
  li s0, -1                       # PMP: permit all accesses (mirrors
  csrw pmpaddr0, s0               # INIT_PMP in the vendored env)
  li s1, 0x1f                     # NAPOT | R | W | X
  csrw pmpcfg0, s1

  la s2, trap_handler
  csrw mtvec, s2

  la sp, irq_stack_top
  la s3, irq_count
  sw x0, 0(s3)
  la s4, nmi_count
  sw x0, 0(s4)

  # Enable maskable interrupts in M-mode.
  li s5, MSTATUS_MIE
  csrs mstatus, s5

  # Rolling checksum over a word array while interrupts fire.
  la s0, test_array
  la s1, test_array_end
  li s2, 0                        # checksum accumulator
  li s3, 256                      # outer iterations
checksum_loop:
  mv s4, s0                       # inner pointer
inner_loop:
  lw s5, 0(s4)
  xor s2, s2, s5
  addi s2, s2, 1
  addi s4, s4, 4
  bne s4, s1, inner_loop
  addi s3, s3, -1
  bnez s3, checksum_loop

  # Stop taking maskable interrupts before checking the results (NMIs may
  # still arrive, so s-registers remain the only live state below).
  li s3, MSTATUS_MIE
  csrc mstatus, s3

  # Recompute the checksum with interrupts off; any corruption from a bad
  # handler shows up here.
  li s6, 0
  mv s7, s0
recheck_loop:
  lw s8, 0(s7)
  xor s6, s6, s8
  addi s6, s6, 1
  addi s7, s7, 4
  bne s7, s1, recheck_loop
  bne s6, s2, fail

  # Interrupts must actually have been taken.
  la s3, irq_count
  lw s3, 0(s3)
  li s4, 20
  blt s3, s4, fail
  # The NMI threshold is 0: with this tb (WritebackStage=0) the NMI
  # take races the co-simulator's NMI model (the DUT retires the in-flight
  # instruction after the irq-only event), so the NMI agent line is off by
  # default. The NMI handler above stays for the WritebackStage=1 variant.
  la s5, nmi_count
  lw s5, 0(s5)
  li s6, 0
  blt s5, s6, fail

  # Pass: TEST_RESULT (1) at the signature address.
  li s0, 1
  li s1, SIGNATURE_ADDR
  sw s0, 0(s1)
done:
  j done

handler_fail:
  # Unexpected exception in the direct handler: report the cause (odd for
  # any real exception code).
  li s0, TOHOST_ADDR
  slli s1, t3, 1
  ori s1, s1, 1
  sw s1, 0(s0)
  j done

fail:
  li s0, TOHOST_ADDR
  li s1, 1
  sw s1, 0(s0)
  j done

  # Anchor the .tohost section (the directed-test link script leaves it
  # empty otherwise, which pulls .data/.bss into the 0x80001000 slot that
  # the environment watches as the tohost handshake address).
  .pushsection .tohost, "aw", @progbits
  .align 6
  .global tohost
tohost:
  .dword 0
  .popsection

  .text

  .data
  .balign 4
test_array:
  .word 0xdeadbeef, 0x12345678, 0x0badc0de, 0x55aa55aa
  .word 0x01020304, 0x89abcdef, 0x00112233, 0xfeedface
  .word 0x80000000, 0x7fffffff, 0x3c3c3c3c, 0xc3c3c3c3
  .word 0x5a5aa5a5, 0x01234567, 0x76543210, 0x0f0f0f0f
test_array_end:

  .bss
  .balign 4
irq_count:
  .word 0
nmi_count:
  .word 0
  .balign 64
irq_stack:
  .skip 256
irq_stack_top:
