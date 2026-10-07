# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Ibex cocotb verification environment: M3 milestone (see cocotb/PLAN.md).
#
# Builds the DUT + the unchanged dv/cosim SpikeCosim comparison core with
# Verilator, compiles a vendored riscv-tests test, a dedicated cocotb
# directed test or a generated random instruction program with the RISC-V
# GCC toolchain, and runs the cocotb testbench with per-instruction
# retirement checking against Spike. The M2/M3 runs enable the randomised
# memory/IRQ agents by default (see the IBEX_COCOTB_* plusargs below).
#
# No existing repository file is modified: the RTL, the dv/cosim checker and
# the directed-test linker script/macros are all consumed in place.
#
# Usage:
#   make sim TEST=addi               # rv32ui (default suite)
#   make sim TESTSUITE=rv32mi TEST=mcsr
#   make sim TESTSUITE=sw TEST=irq_test
#   make sim TESTSUITE=sw TEST=error_test
#   make sim TESTSUITE=gen TEST=1    # random program, TEST is the seed
#   make build_test TEST=addi        # only build the test binary

IBEX_ROOT  ?= $(abspath ..)
COCOTB_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

# --- Tools (defaults assume the setup described in cocotb/PLAN.md) ---
RISCV_GCC         ?= $(HOME)/tools/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc
RISCV_OBJCOPY     ?= $(dir $(RISCV_GCC))riscv-none-elf-objcopy
SPIKE_PREFIX      ?= $(HOME)/tools/spike

# --- cocotb flow ---
SIM ?= verilator
TOPLEVEL_LANG = verilog
TOPLEVEL = ibex_cocotb_tb
MODULE = tests.test_core
COCOTB_TESTCASE = $(TESTCASE)
COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

# --- Test selection ---
# TESTSUITE selects the program source and the cocotb test function:
#   rv32ui/rv32mi  vendored riscv-tests isa suites
#   sw             dedicated cocotb directed tests (tests/sw/*.S)
#   gen            generated random programs (gen/instr_gen.py, TEST=seed)
TESTSUITE ?= rv32ui
TEST ?= addi
OUT_DIR ?= out

# The poisoned address for the dedicated error test. This is its single
# definition: it is compiled into the program (-DERROR_ADDR) and passed to
# the memory agent (+ibex_cocotb_error_addrs, consumed by py/tb_env.py).
IBEX_ERROR_ADDR ?= 0xDEAD0000

# Extra cocotb plusargs appended by a TESTSUITE branch (e.g. the error
# test's poisoned address); IBEX_COCOTB_OPTS remains the user override.
EXTRA_PLUSARGS ?=

ifeq ($(TESTSUITE),rv32ui)
  TESTCASE := test_rv32ui
  SW_TEST_SRC := $(IBEX_ROOT)/vendor/riscv-tests/isa/rv32ui/$(TEST).S
  SW_TEST_INC := -I$(IBEX_ROOT)/vendor/riscv-test-env/ \
                 -I$(IBEX_ROOT)/vendor/riscv-test-env/p/ \
                 -I$(IBEX_ROOT)/vendor/riscv-tests/isa/macros/scalar/ \
                 -I$(IBEX_ROOT)/dv/uvm/core_ibex/directed_tests \
                 -include $(COCOTB_DIR)/tests/sw/legacy_csr_compat.h
else ifeq ($(TESTSUITE),rv32mi)
  TESTCASE := test_rv32mi
  SW_TEST_SRC := $(IBEX_ROOT)/vendor/riscv-tests/isa/rv32mi/$(TEST).S
  SW_TEST_INC := -I$(IBEX_ROOT)/vendor/riscv-test-env/ \
                 -I$(IBEX_ROOT)/vendor/riscv-test-env/p/ \
                 -I$(IBEX_ROOT)/vendor/riscv-tests/isa/macros/scalar/ \
                 -I$(IBEX_ROOT)/dv/uvm/core_ibex/directed_tests \
                 -include $(COCOTB_DIR)/tests/sw/legacy_csr_compat.h
else ifeq ($(TESTSUITE),sw)
  SW_TEST_SRC := $(COCOTB_DIR)/tests/sw/$(TEST).S
  SW_TEST_INC :=
  ifeq ($(TEST),irq_test)
    TESTCASE := test_irq
  else ifeq ($(TEST),error_test)
    TESTCASE := test_error
    # IBEX_ERROR_ADDR is the single definition of the poisoned address
    # (see above): compile it into the program and pass it to the memory
    # agent through the plusarg (plain hex, parsed by py/tb_env.py).
    SW_TEST_INC := -DERROR_ADDR=$(IBEX_ERROR_ADDR)
    EXTRA_PLUSARGS := +ibex_cocotb_error_addrs=$(subst 0x,,$(IBEX_ERROR_ADDR))
  else
    $(error unknown sw test '$(TEST)')
  endif
else ifeq ($(TESTSUITE),gen)
  TESTCASE := test_random
  SW_TEST_SRC := $(OUT_DIR)/gen_$(TEST).S
  SW_TEST_INC :=
else
  $(error unknown TESTSUITE '$(TESTSUITE)')
endif

# Random instruction programs (M3, see gen/instr_gen.py): the source is
# generated per seed (TEST is the seed, encoded in the file name, so a new
# seed always regenerates; editing the generator also rebuilds it).
$(OUT_DIR)/gen_%.S: gen/instr_gen.py | $(OUT_DIR)
	python3 gen/instr_gen.py --seed $* --out $@

BIN := $(OUT_DIR)/$(TESTSUITE)_$(TEST).bin

# Agent randomisation knobs are declared by the tests on IbexCocotbConfig
# (tests/test_core.py); the plusargs below are only an override mechanism,
# applied in one place in py/tb_env.py. Spurious responses are supported by
# the agent but stay off on this tb: its SecureIbex=0 core does not gate
# stray bus responses, so tb_env forces them off (the same rule as the UVM
# base test). Leave IBEX_COCOTB_OPTS empty unless overriding a knob for a
# one-off run.
#   +ibex_cocotb_zero_delays=1    force all memory delays to zero
#   +ibex_cocotb_zero_delays=0    force the random delay distribution
#   +ibex_cocotb_spurious_resp=1  request spurious dside responses
#   +ibex_cocotb_irq=0            disable the IRQ agent
#   +ibex_cocotb_irq_nmi=1        include the NMI line in the IRQ agent
IBEX_COCOTB_OPTS ?=

# Image load base address: the Python memory model loads the binary here and
# the tb (CMD_INIT) uses the same plusarg for the co-simulator backdoor
# write, so this Makefile is the single source for both.
IBEX_LOAD_ADDR ?= 80000000

# The tb is elaborated first; ibex_rtl.f carries the RTL/prims file list.
VERILOG_SOURCES = $(COCOTB_DIR)/tb/ibex_cocotb_tb.sv

# Verilator options for the elaboration step. The lint waiver must come
# before any other Verilog file; --timing is required because the design
# uses the SV-generated clock and the ibex_top clock-gate latch.
VERILATOR_OPTS = \
  $(IBEX_ROOT)/lint/verilator_waiver.vlt \
  +define+RVFI \
  -f $(COCOTB_DIR)/ibex_rtl.f \
  --timing \
  --unroll-count 72 \
  -Wno-fatal

# The unchanged dv/cosim DPI sources plus the cocotb helper.
COSIM_SRCS = \
  $(IBEX_ROOT)/dv/cosim/cosim_dpi.cc \
  $(IBEX_ROOT)/dv/cosim/spike_cosim.cc \
  $(IBEX_ROOT)/dv/uvm/core_ibex/common/ibex_cosim_agent/spike_cosim_dpi.cc \
  $(COCOTB_DIR)/dpi/ibex_cocotb_dpi.cc

# Link the SpikeCosim comparison core (ibex_cosim branch of Spike).
COSIM_LDFLAGS = -L$(SPIKE_PREFIX)/lib -Wl,-rpath,$(SPIKE_PREFIX)/lib -lriscv -lsoftfloat -ldisasm -lfesvr -lfdt -ldl -lpthread

COMPILE_ARGS = \
  $(VERILATOR_OPTS) \
  -CFLAGS "-std=c++17" \
  $(COSIM_SRCS) \
  -LDFLAGS "$(COSIM_LDFLAGS)"

# Runtime plusargs consumed by tb/ibex_cocotb_tb.sv (CMD_INIT) to load the
# binary into the co-simulator, by the Python test for its own copy of the
# binary path and image base address, and by py/tb_env.py for the agent
# knobs. The binary travels through this one channel so the Python memory
# model and the co-simulator can never hold different images.
COCOTB_PLUSARGS = +ibex_cocotb_bin=$(BIN) +ibex_cocotb_load_addr=$(IBEX_LOAD_ADDR) $(EXTRA_PLUSARGS) $(IBEX_COCOTB_OPTS)

# C++ include paths for the DPI sources. Verilator's generated makefile uses
# $(CPPFLAGS) from the environment (the -I entries cannot be passed through
# -CFLAGS, where Verilator strips them).
export CPPFLAGS = -I$(IBEX_ROOT)/dv/cosim -I$(IBEX_ROOT)/dv/uvm/core_ibex/common/ibex_cosim_agent -I$(SPIKE_PREFIX)/include -I$(SPIKE_PREFIX)/include/softfloat -I$(SPIKE_PREFIX)/include/fesvr -I$(SPIKE_PREFIX)/include/fdt

# Rebuild/rerun the simulation when the test binary changes.
CUSTOM_SIM_DEPS = $(BIN)

# --- Test binary build ---
# GCC options mirror dv/uvm/core_ibex/directed_tests/directed_testlist.yaml
# (riscv-tests config); the linker script is the directed-test link.ld.
$(BIN): $(SW_TEST_SRC) | $(OUT_DIR)
	$(RISCV_GCC) -march=rv32imc_zicsr -mabi=ilp32 -static -mcmodel=medany \
	  -fvisibility=hidden -nostdlib -nostartfiles $(SW_TEST_INC) \
	  -T $(IBEX_ROOT)/dv/uvm/core_ibex/directed_tests/link.ld \
	  -o $(OUT_DIR)/$(TESTSUITE)_$(TEST).elf $<
	$(RISCV_OBJCOPY) -O binary $(OUT_DIR)/$(TESTSUITE)_$(TEST).elf $@

$(OUT_DIR):
	mkdir -p $@

.PHONY: build_test

build_test: $(BIN)

include $(shell python3 -m cocotb_tools.config --makefiles)/Makefile.sim
