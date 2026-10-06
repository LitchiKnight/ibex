# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Ibex cocotb verification environment: M1 milestone (see cocotb/PLAN.md).
#
# Builds the DUT + the unchanged dv/cosim SpikeCosim comparison core with
# Verilator, compiles a vendored riscv-tests rv32ui test with the RISC-V GCC
# toolchain, and runs the cocotb testbench with per-instruction retirement
# checking against Spike.
#
# No existing repository file is modified: the RTL, the dv/cosim checker and
# the directed-test linker script/macros are all consumed in place.
#
# Usage:
#   make sim TEST=addi            # build test binary, run cocotb test
#   make build_test TEST=addi     # only build the test binary

IBEX_ROOT  ?= $(abspath ..)
COCOTB_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

# --- Tools (defaults assume the M1 setup described in cocotb/PLAN.md) ---
RISCV_GCC         ?= $(HOME)/tools/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc
RISCV_OBJCOPY     ?= $(dir $(RISCV_GCC))riscv-none-elf-objcopy
SPIKE_PREFIX      ?= $(HOME)/tools/spike

# --- cocotb flow ---
SIM ?= verilator
TOPLEVEL_LANG = verilog
TOPLEVEL = ibex_cocotb_tb
MODULE = tests.test_rv32ui
COCOTB_HDL_TIMEUNIT = 1ns
COCOTB_HDL_TIMEPRECISION = 1ps

# --- Test selection ---
TEST ?= addi
OUT_DIR ?= out
BIN := $(OUT_DIR)/rv32ui_$(TEST).bin

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
# binary into the co-simulator, and by the Python test for its own copy of
# the image base address.
COCOTB_PLUSARGS = +ibex_cocotb_bin=$(BIN) +ibex_cocotb_load_addr=$(IBEX_LOAD_ADDR)

# Environment for the Python testbench.
export IBEX_COCOTB_BIN = $(BIN)

# C++ include paths for the DPI sources. Verilator's generated makefile uses
# $(CPPFLAGS) from the environment (the -I entries cannot be passed through
# -CFLAGS, where Verilator strips them).
export CPPFLAGS = -I$(IBEX_ROOT)/dv/cosim -I$(IBEX_ROOT)/dv/uvm/core_ibex/common/ibex_cosim_agent -I$(SPIKE_PREFIX)/include -I$(SPIKE_PREFIX)/include/softfloat -I$(SPIKE_PREFIX)/include/fesvr -I$(SPIKE_PREFIX)/include/fdt

# Rebuild/rerun the simulation when the test binary changes.
CUSTOM_SIM_DEPS = $(BIN)

# --- Test binary build (rv32ui from vendored riscv-tests) ---
# GCC options mirror dv/uvm/core_ibex/directed_tests/directed_testlist.yaml
# (riscv-tests config); the linker script is the directed-test link.ld.
TEST_INC_DIRS = -I$(IBEX_ROOT)/vendor/riscv-test-env/ \
                -I$(IBEX_ROOT)/vendor/riscv-test-env/p/ \
                -I$(IBEX_ROOT)/vendor/riscv-tests/isa/macros/scalar/ \
                -I$(IBEX_ROOT)/dv/uvm/core_ibex/directed_tests

$(BIN): $(IBEX_ROOT)/vendor/riscv-tests/isa/rv32ui/$(TEST).S | $(OUT_DIR)
	$(RISCV_GCC) -march=rv32imc_zicsr -mabi=ilp32 -static -mcmodel=medany \
	  -fvisibility=hidden -nostdlib -nostartfiles $(TEST_INC_DIRS) \
	  -T $(IBEX_ROOT)/dv/uvm/core_ibex/directed_tests/link.ld \
	  -o $(OUT_DIR)/rv32ui_$(TEST).elf $<
	$(RISCV_OBJCOPY) -O binary $(OUT_DIR)/rv32ui_$(TEST).elf $@

$(OUT_DIR):
	mkdir -p $@

.PHONY: build_test

build_test: $(BIN)

include $(shell python3 -m cocotb_tools.config --makefiles)/Makefile.sim
