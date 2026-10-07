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
# On this tb (WritebackStage=0) mie stays at its reset value, so no
# maskable interrupt can actually be taken: a real take presents RVFI
# shapes the co-simulator protocol does not support (the UVM flow
# validates interrupt takes only on WritebackStage=1). The agent's raises
# are still observable through the RVFI pre/post mip fields, which the
# scoreboard feeds to the co-simulator on every step. The test therefore
# asserts the checksum is intact under that mip noise and that the
# handler was entered exactly zero times; the WritebackStage=1 variant
# enables mie and flips the check to a minimum entry count.
#
# The result uses the same handshake as the vendored tests: a write of 1
# to the signature address (0x8ffffff8) on pass, an odd write to the
# tohost section (0x80001000) on fail (see mem_agent.classify_test_result).

#define SIGNATURE_ADDR 0x8ffffff8
#define TOHOST_ADDR    0x80001000
#define MSTATUS_MIE    0x8

# Minimum NMI handler entries the test demands. The threshold is 0 on this
# tb (WritebackStage=0): the DUT retires an in-flight instruction after the
# irq-only event, which the co-simulator's NMI model cannot match, so the
# NMI agent line is off by default (see py/irq_agent.py). A
# WritebackStage=1 variant overrides the threshold with -DNMI_EXPECT_MIN=20
# without touching this file.
#ifndef NMI_EXPECT_MIN
#define NMI_EXPECT_MIN 0
#endif

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

  # Enable maskable interrupts in M-mode. mie is deliberately left at its
  # reset value (0): on this tb (WritebackStage=0) a real interrupt take
  # presents RVFI shapes the co-simulator protocol does not support (the
  # UVM flow validates interrupt takes only on WritebackStage=1), so the
  # agent's raises stay observable through the RVFI pre/post mip fields
  # but never trap. See PLAN.md section 9.
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
  # handler shows up here. The accumulation must mirror the interruptible
  # pass exactly - the same 256 outer iterations of (acc ^ word) + 1 per
  # element - because that interleaving is not idempotent under
  # repetition.
  li s6, 0
  li s9, 256
recheck_outer:
  mv s7, s0
recheck_loop:
  lw s8, 0(s7)
  xor s6, s6, s8
  addi s6, s6, 1
  addi s7, s7, 4
  bne s7, s1, recheck_loop
  addi s9, s9, -1
  bnez s9, recheck_outer
  bne s6, s2, fail_checksum

  # No handler entry may have happened: with mie at its reset value no
  # maskable interrupt can be taken, so the handler can only have been
  # entered by a bug (an unexpected trap takes the handler_fail path
  # instead). The WritebackStage=1 variant flips this check to a minimum
  # entry count (see the NMI_EXPECT_MIN define above).
  la s3, irq_count
  lw s3, 0(s3)
  li s4, 0
  bne s3, s4, fail_irq_count
  # The NMI threshold is NMI_EXPECT_MIN (0 on this tb, see the define
  # above); the NMI handler stays for the WritebackStage=1 variant.
  la s5, nmi_count
  lw s5, 0(s5)
  li s6, NMI_EXPECT_MIN
  blt s5, s6, fail_nmi

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

# The failure paths write distinct odd values (3/5/7), so a failing run's
# tohost write names the failed check. None may write 1: the environment
# classifies a tohost write of 1 as pass (the vendored ecall/scall
# convention).
fail_checksum:
  li s0, TOHOST_ADDR
  li s1, 3
  sw s1, 0(s0)
  j done

fail_irq_count:
  li s0, TOHOST_ADDR
  li s1, 5
  sw s1, 0(s0)
  j done

fail_nmi:
  li s0, TOHOST_ADDR
  li s1, 7
  sw s1, 0(s0)
  j done

fail:
  li s0, TOHOST_ADDR
  li s1, 9
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
