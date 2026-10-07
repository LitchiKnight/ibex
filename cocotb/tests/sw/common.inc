# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Shared layout, initialisation and handshake for the dedicated cocotb
# directed tests (included by the .S files next to it; the file is passed
# through cpp, so no comment may contain the two characters slash-star).
# The layout of .text.init (linked at 0x80000000 by
# dv/uvm/core_ibex/directed_tests/link.ld):
#   0x80   _start
#   0x100  trap handler (mtvec base, set by DIRECTED_TEST_INIT)
#   0x200  reset_vector (the including file places it there)
#
# The result handshake follows the vendored tests: a write of 1 to the
# signature address on pass, an odd write to the tohost section on fail
# (see py/mem_agent.py TestHandshake.classify).

#define SIGNATURE_ADDR 0x8ffffff8
#define TOHOST_ADDR    0x80001000

# The entry stub at 0x80; the including file defines reset_vector.
.macro DIRECTED_TEST_ENTRY
  .section .text.init
  .option norvc

  .globl _start
  .org 0x80
_start:
  j reset_vector
.endm

# PMP and trap-vector setup for reset_vector to run first (mirrors
# INIT_PMP in the vendored riscv-test-env). Uses s0-s2; the including
# file's reset_vector continues with s3 onwards.
.macro DIRECTED_TEST_INIT
  li s0, -1                       # PMP: permit all accesses
  csrw pmpaddr0, s0
  li s1, 0x1f                     # NAPOT | R | W | X
  csrw pmpcfg0, s1

  la s2, trap_handler
  csrw mtvec, s2
.endm

# Anchor the .tohost section (the directed-test link script leaves it
# empty otherwise, which pulls .bss into the 0x80001000 slot that the
# environment watches as the tohost handshake address).
.macro DIRECTED_TEST_TOHOST_ANCHOR
  .pushsection .tohost, "aw", @progbits
  .align 6
  .global tohost
tohost:
  .dword 0
  .popsection
.endm
