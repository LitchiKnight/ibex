#!/usr/bin/env python3
# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Random instruction program generator (M3, see cocotb/PLAN.md).

Generates a self-contained assembly program whose instruction stream is
picked from weighted categories, modelled on the riscv-dv instruction mix
(dv/uvm/core_ibex/riscv_dv_extension/): ALU / ALU-imm / load / store /
branch / multiply / CSR / jump / compressed. The program runs on the DUT
while the environment compares every retired instruction against the
SpikeCosim co-simulator, and it self-checks its own execution:

- deliberately injected illegal instructions must each trap with
  mcause == 2 from inside the dedicated illegal section; the trap handler
  counts the traps and skips past each faulting word, and the epilogue
  compares the count against EXPECTED_ILLEGAL;
- reaching the end handshake (a write of 1 to the signature address
  0x8ffffff8, the same convention as the vendored tests) proves the whole
  stream executed; any unexpected trap writes an odd value to the tohost
  section (0x80001000) and fails.

Guarantees that keep random programs free of false positives:

- termination: every intra-stream jump (branch, jal, jalr) targets a
  strictly later label, so no cycle can form inside the stream; the only
  backward edge is the outer loop's bnez, bounded by ITERATIONS. The
  iteration counter lives in s11, which the stream never writes;
- isolation: loads and stores are confined to a dedicated data pool
  (``la t, data_pool`` + offset), so the stream can never write to the
  handshake addresses or to the code;
- comparability: the CSR category is restricted to mscratch, the one CSR
  whose value both the DUT and the co-simulator model identically for
  every access pattern (the riscv-dv template comments out the counter
  CSRs for the same reason: they caused cosim mismatches); all generated
  instructions are 32-bit except the conservative compressed subset, and
  every compressed load/store carries its own pool base, so nothing
  depends on a register surviving across the stream.

The layout mirrors tests/sw/irq_test.S (.text.init linked at 0x80000000 by
dv/uvm/core_ibex/directed_tests/link.ld):

  0x80   _start
  0x100  trap handler (mtvec base, set by reset_vector)
  0x200  reset_vector

The register convention reserves nothing from the stream: the handler only
clobbers t0-t2 (architecturally fine, and the co-simulator executes the
same handler code so both sides stay in lockstep). The stream runs with
interrupts disabled (mstatus.MIE stays clear), so the handler only ever
sees the injected illegal instructions.

Usage:
  python3 gen/instr_gen.py --seed 1 --out out/gen_1.S
"""

import argparse
import random

# --- instruction mix --------------------------------------------------------
# Category weights for the random stream, modelled on the riscv-dv main
# instruction stream distribution (LOAD/STORE/BRANCH/JAL/ALU mix). The
# compressed category uses a conservative RV32C subset.
INSTR_MIX = {
    "alu": 20,      # register-register ALU
    "alui": 20,     # ALU with immediate
    "load": 12,
    "store": 8,
    "branch": 10,
    "mul": 8,       # RV32M (the tb config is RV32MFast)
    "csr": 4,       # mscratch only, see the module docstring
    "jal": 4,
    "jalr": 2,      # la + jalr to a known label
    "comp": 12,     # compressed subset
}

# Only mscratch may be accessed: see the comparability note above.
CSR_NAME = "mscratch"

# Guaranteed-illegal 32-bit words. 0x00000000 has bits[1:0] == 00 (neither
# a legal 32-bit opcode nor a legal halfword: the all-zero RVC encoding is
# reserved); the 0x7F major opcode of the other two is reserved in RV32.
# All three trap in both Ibex and Spike with an identical mtval.
ILLEGAL_WORDS = (0x00000000, 0xFFFFFFFF, 0x00007FFF)

ALU_OPS = ("add", "sub", "slt", "sltu", "and", "or", "xor",
           "sll", "srl", "sra")
ALUI_OPS = ("addi", "slti", "sltiu", "andi", "ori", "xori",
            "slli", "srli", "srai")
MUL_OPS = ("mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu")
LOAD_OPS = ((4, "lw"), (2, "lh"), (2, "lhu"), (1, "lb"), (1, "lbu"))
STORE_OPS = ((4, "sw"), (2, "sh"), (1, "sb"))
BRANCH_OPS = ("beq", "bne", "blt", "bge", "bltu", "bgeu")

GPRS = ("zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
        "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7",
        "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11",
        "t3", "t4", "t5", "t6")
# s11 is the outer-loop iteration counter and must never be written by the
# stream; x0 as rd would turn an instruction into a no-op variant.
STREAM_RD_POOL = tuple(g for g in GPRS if g not in ("zero", "s11"))
# jal/jalr may also write x0 (a plain jump).
JUMP_RD_POOL = STREAM_RD_POOL + ("zero",)
# Compressed c.lw/c.sw only encode x8-x15.
COMP_POOL = GPRS[8:16]
# c.li/c.addi with rd == sp encode the addi16sp forms with a different
# immediate range; keep sp out to avoid encoding edge cases.
COMP_IMM_RD_POOL = tuple(g for g in STREAM_RD_POOL if g != "sp")


class Generator:
    """One deterministic program generator (all randomness from ``seed``)."""

    def __init__(self, seed, stream_len=400, iterations=10, num_illegal=5,
                 block_size=8, pool_size=2048):
        if stream_len < 2:
            raise ValueError("stream_len must be at least 2")
        self.seed = seed
        self.stream_len = stream_len
        self.iterations = iterations
        self.num_illegal = num_illegal
        self.pool_size = pool_size
        # At least two label blocks so forward targets always exist.
        self.block_size = max(1, min(block_size, stream_len // 2))
        self.rng = random.Random(seed)
        self._labels = ()
        self._block = 0

    # -- register/operand selection -------------------------------------------

    def _gpr(self, pool=GPRS):
        return self.rng.choice(pool)

    def _imm12(self):
        return self.rng.randrange(-2048, 2048)

    def _shift(self):
        return self.rng.randrange(0, 32)

    def _csr_imm(self):
        return self.rng.randrange(0, 32)

    def _comp_imm(self):
        return self.rng.choice([i for i in range(-32, 32) if i])

    def _pool_offset(self, align):
        return self.rng.randrange(0, self.pool_size // align) * align

    def _comp_offset(self):
        return self.rng.randrange(0, 32) * 4

    def _forward_label(self):
        return self.rng.choice(self._labels[self._block + 1:])

    # -- instruction emission (each returns the asm lines of one slot) -------

    def _emit_alu(self):
        op = self.rng.choice(ALU_OPS)
        return ["{} {}, {}, {}".format(op, self._gpr(STREAM_RD_POOL),
                                       self._gpr(), self._gpr())]

    def _emit_alui(self):
        op = self.rng.choice(ALUI_OPS)
        imm = self._shift() if op in ("slli", "srli", "srai") else self._imm12()
        return ["{} {}, {}, {}".format(op, self._gpr(STREAM_RD_POOL),
                                       self._gpr(), imm)]

    def _emit_mul(self):
        op = self.rng.choice(MUL_OPS)
        return ["{} {}, {}, {}".format(op, self._gpr(STREAM_RD_POOL),
                                       self._gpr(), self._gpr())]

    def _emit_load(self):
        align, op = self.rng.choice(LOAD_OPS)
        base = self._gpr(STREAM_RD_POOL)
        return ["la {}, data_pool".format(base),
                "{} {}, {}({})".format(op, self._gpr(STREAM_RD_POOL),
                                       self._pool_offset(align), base)]

    def _emit_store(self):
        align, op = self.rng.choice(STORE_OPS)
        base = self._gpr(STREAM_RD_POOL)
        return ["la {}, data_pool".format(base),
                "{} {}, {}({})".format(op, self._gpr(),
                                       self._pool_offset(align), base)]

    def _emit_branch(self):
        op = self.rng.choice(BRANCH_OPS)
        return ["{} {}, {}, {}".format(op, self._gpr(), self._gpr(),
                                       self._forward_label())]

    def _emit_jal(self):
        return ["jal {}, {}".format(self._gpr(JUMP_RD_POOL),
                                    self._forward_label())]

    def _emit_jalr(self):
        base = self._gpr(STREAM_RD_POOL)
        return ["la {}, {}".format(base, self._forward_label()),
                "jalr {}, {}, 0".format(self._gpr(JUMP_RD_POOL), base)]

    def _emit_csr(self):
        form = self.rng.choice(("csrr", "csrrw", "csrrwi", "csrrs", "csrrc"))
        rd = self._gpr(STREAM_RD_POOL)
        if form == "csrr":
            return ["{} {}, {}".format(form, rd, CSR_NAME)]
        if form == "csrrwi":
            return ["{} {}, {}, {}".format(form, rd, CSR_NAME,
                                           self._csr_imm())]
        return ["{} {}, {}, {}".format(form, rd, CSR_NAME, self._gpr())]

    def _emit_comp(self):
        kind = self.rng.choice(("li", "mv", "addi", "lw", "sw", "nop"))
        if kind == "nop":
            return ["c.nop"]
        if kind == "li":
            return ["c.li {}, {}".format(self._gpr(COMP_IMM_RD_POOL),
                                         self._comp_imm())]
        if kind == "mv":
            return ["c.mv {}, {}".format(self._gpr(STREAM_RD_POOL),
                                         self._gpr(STREAM_RD_POOL))]
        if kind == "addi":
            return ["c.addi {}, {}".format(self._gpr(COMP_IMM_RD_POOL),
                                           self._comp_imm())]
        # c.lw/c.sw carry their own pool base (nothing may depend on a
        # register surviving across the stream): x8-x15 only, 4-byte
        # aligned offsets.
        return ["la s0, data_pool",
                "c.{} {}, {}(s0)".format(kind, self._gpr(COMP_POOL),
                                         self._comp_offset())]

    # -- stream construction ---------------------------------------------------

    def _stream_lines(self):
        num = self.stream_len
        block_size = self.block_size
        self._labels = ["L{}".format(i)
                        for i in range((num + block_size - 1) // block_size)]
        lines = []
        for index in range(num):
            if index % block_size == 0:
                lines.append("L{}:".format(index // block_size))
            self._block = index // block_size
            category = self.rng.choices(tuple(INSTR_MIX),
                                        weights=tuple(INSTR_MIX.values()))[0]
            if (category in ("branch", "jal", "jalr")
                    and self._block + 1 >= len(self._labels)):
                # The last block has no forward label; jump categories fall
                # back to a plain ALU slot so no cycle can form.
                category = "alu"
            lines.extend(getattr(self, "_emit_" + category)())
        return lines

    # -- full program -----------------------------------------------------------

    def generate(self) -> str:
        illegal = [self.rng.choice(ILLEGAL_WORDS)
                   for _ in range(self.num_illegal)]
        parts = [
            "# Generated by gen/instr_gen.py: seed={} stream_len={} "
            "iterations={} num_illegal={} block_size={} pool_size={}".format(
                self.seed, self.stream_len, self.iterations,
                self.num_illegal, self.block_size, self.pool_size),
            "# Register convention: s11 is the outer-loop counter (the "
            "stream never writes it);",
            "# the trap handler clobbers only t0-t2 and needs no saved "
            "state.",
            "",
            "#define SIGNATURE_ADDR 0x8ffffff8",
            "#define TOHOST_ADDR    0x80001000",
            "#define CAUSE_ILLEGAL_INSTRUCTION 2",
            "#define ITERATIONS {}".format(self.iterations),
            "#define EXPECTED_ILLEGAL {}".format(self.num_illegal),
            "",
            "  .section .text.init",
            "  .option norvc",
            "",
            "  .globl _start",
            "  .org 0x80",
            "_start:",
            "  j reset_vector",
            "",
            "  .org 0x100",
            "trap_handler:",
            "  # Only the deliberately injected illegal instructions may "
            "trap: mcause == 2",
            "  # with mepc inside [illegal_start, illegal_end); anything "
            "else fails.",
            "  # Uses only t0-t2, which the stream holds no state in.",
            "  csrr t0, mcause",
            "  li t1, CAUSE_ILLEGAL_INSTRUCTION",
            "  bne t0, t1, trap_fail",
            "  csrr t0, mepc",
            "  la t1, illegal_start",
            "  la t2, illegal_end",
            "  blt t0, t1, trap_fail",
            "  bge t0, t2, trap_fail",
            "  addi t0, t0, 4                 # skip the faulting 32-bit word",
            "  csrw mepc, t0",
            "  la t1, trap_count",
            "  lw t0, 0(t1)",
            "  addi t0, t0, 1",
            "  sw t0, 0(t1)",
            "  mret",
            "trap_fail:",
            "  csrr t0, mcause               # odd write = failure cause",
            "  li t1, TOHOST_ADDR",
            "  slli t0, t0, 1",
            "  ori t0, t0, 1",
            "  sw t0, 0(t1)",
            "1:",
            "  j 1b",
            "",
            "  .org 0x200",
            "reset_vector:",
            "  # PMP: permit all accesses (mirrors INIT_PMP in the "
            "vendored env).",
            "  li s0, -1",
            "  csrw pmpaddr0, s0",
            "  li s1, 0x1f                     # NAPOT | R | W | X",
            "  csrw pmpcfg0, s1",
            "  la s2, trap_handler",
            "  csrw mtvec, s2",
            "  la sp, stack_top",
            "  la s3, trap_count",
            "  sw x0, 0(s3)",
            "  j main",
            "",
            "  # Anchor the .tohost section (the directed-test link script "
            "leaves it",
            "  # empty otherwise, which pulls .data into the 0x80001000 "
            "slot that the",
            "  # environment watches as the tohost handshake address).",
            "  .pushsection .tohost, \"aw\", @progbits",
            "  .align 6",
            "  .global tohost",
            "tohost:",
            "  .dword 0",
            "  .popsection",
            "",
            "  .text",
            "  .option rvc",
            "",
            "main:",
            "  # The injected illegal instructions: executed sequentially "
            "and unreachable",
            "  # by any jump (all stream jumps are forward from the loop), "
            "so exactly",
            "  # EXPECTED_ILLEGAL traps are taken.",
            "illegal_start:",
        ]
        for word in illegal:
            parts.append("  .word 0x{:08x}".format(word))
        parts += [
            "illegal_end:",
            "  li s11, ITERATIONS",
            "main_loop:",
        ]
        for line in self._stream_lines():
            parts.append(line if line.endswith(":") else "  " + line)
        parts += [
            "  addi s11, s11, -1",
            "  bnez s11, main_loop",
            "",
            "  # The trap handler must have been entered exactly once per "
            "injected word.",
            "  la s2, trap_count",
            "  lw s2, 0(s2)",
            "  li s3, EXPECTED_ILLEGAL",
            "  bne s2, s3, fail",
            "",
            "  # Pass: TEST_RESULT (1) at the signature address.",
            "  li s0, 1",
            "  li s1, SIGNATURE_ADDR",
            "  sw s0, 0(s1)",
            "done:",
            "  j done",
            "",
            "fail:",
            "  # 3, not 1: the environment classifies a tohost write of 1",
            "  # as pass (the vendored ecall/scall convention), so a",
            "  # generated program's failure must be a different odd value.",
            "  li s0, TOHOST_ADDR",
            "  li s1, 3",
            "  sw s1, 0(s0)",
            "  j done",
            "",
            "  .data",
            "  .balign 4",
            "data_pool:",
            "  .skip {}".format(self.pool_size),
            "",
            "  .bss",
            "  .balign 4",
            "trap_count:",
            "  .word 0",
            "  .balign 64",
            "stack:",
            "  .skip 256",
            "stack_top:",
            "",
        ]
        return "\n".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a random self-checking Ibex test program "
                    "(see the module docstring for the guarantees)")
    parser.add_argument("--seed", type=int, required=True,
                        help="random seed (deterministic output)")
    parser.add_argument("--out", required=True, help="output .S path")
    parser.add_argument("--stream-len", type=int, default=400,
                        help="instructions per outer iteration")
    parser.add_argument("--iterations", type=int, default=10,
                        help="outer loop count")
    parser.add_argument("--num-illegal", type=int, default=5,
                        help="injected illegal instructions")
    parser.add_argument("--block-size", type=int, default=8,
                        help="instructions per label block")
    parser.add_argument("--pool-size", type=int, default=2048,
                        help="data pool size in bytes")
    args = parser.parse_args(argv)
    generator = Generator(args.seed, stream_len=args.stream_len,
                          iterations=args.iterations,
                          num_illegal=args.num_illegal,
                          block_size=args.block_size,
                          pool_size=args.pool_size)
    text = generator.generate()
    with open(args.out, "w") as handle:
        handle.write(text)
    print("wrote {} (seed={}, stream_len={}, iterations={}, "
          "num_illegal={})".format(args.out, args.seed, args.stream_len,
                                   args.iterations, args.num_illegal))


if __name__ == "__main__":
    main()
