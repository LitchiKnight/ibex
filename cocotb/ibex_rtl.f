// Copyright lowRISC contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// Verilator source list for the cocotb verification environment (cocotb/).
//
// This list is derived from dv/uvm/core_ibex/ibex_dv.f (the UVM DV file
// list), keeping only the RTL, the primitives and the packages needed to
// elaborate `ibex_top`; the UVM/DV testbench files are replaced by
// cocotb/tb/ibex_cocotb_tb.sv. All paths are relative to the cocotb/
// directory. The existing sources are consumed in place, never modified.

+incdir+../rtl
+incdir+../vendor/lowrisc_ip/ip/prim/rtl
+incdir+../vendor/lowrisc_ip/dv/sv/dv_utils
+incdir+../dv/cosim
+incdir+../dv/uvm/core_ibex/common/ibex_cosim_agent

// Primitives (mirrors the ibex_dv.f prim list).
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_pkg.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_assert.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_util_pkg.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_count_pkg.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_count.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_pkg.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_22_16_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_22_16_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_64_57_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_64_57_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_hamming_22_16_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_hamming_22_16_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_hamming_39_32_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_hamming_39_32_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_hamming_72_64_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_hamming_72_64_enc.sv

../vendor/lowrisc_ip/ip/prim/rtl/prim_mubi_pkg.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_ram_1p_pkg.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_ram_1p_adv.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_ram_1p_scr.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_ram_1p.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_clock_gating.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_buf.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_clock_mux2.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_flop.sv
../vendor/lowrisc_ip/ip/prim_generic/rtl/prim_and2.sv

// Shared lowRISC code (mirrors the ibex_dv.f shared list).
../vendor/lowrisc_ip/ip/prim/rtl/prim_cipher_pkg.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_lfsr.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_28_22_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_28_22_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_39_32_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_39_32_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_64_57_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_64_57_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_72_64_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_inv_72_64_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_prince.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_subst_perm.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_28_22_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_28_22_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_39_32_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_39_32_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_72_64_enc.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_secded_72_64_dec.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_fifo_sync_cnt.sv
../vendor/lowrisc_ip/ip/prim/rtl/prim_fifo_sync.sv

// Vendored PULP common cells (used by the TRVK filter).
../vendor/pulp_common_cells/rtl/stream_fork.sv
../vendor/pulp_common_cells/rtl/stream_join_dynamic.sv

// Ibex core RTL (mirrors the ibex_dv.f core list, without the tracer and the
// tracing top).
../rtl/ibex_pkg.sv
../rtl/ibex_cheriot_pkg.sv
../rtl/ibex_cheriot_ex.sv
../rtl/ibex_alu.sv
../rtl/ibex_branch_predict.sv
../rtl/ibex_compressed_decoder.sv
../rtl/ibex_controller.sv
../rtl/ibex_csr.sv
../rtl/ibex_cs_registers.sv
../rtl/ibex_counter.sv
../rtl/ibex_decoder.sv
../rtl/ibex_dummy_instr.sv
../rtl/ibex_ex_block.sv
../rtl/ibex_wb_stage.sv
../rtl/ibex_id_stage.sv
../rtl/ibex_icache.sv
../rtl/ibex_if_stage.sv
../rtl/ibex_load_store_unit.sv
../rtl/ibex_lockstep.sv
../rtl/ibex_multdiv_slow.sv
../rtl/ibex_multdiv_fast.sv
../rtl/ibex_prefetch_buffer.sv
../rtl/ibex_fetch_fifo.sv
../rtl/ibex_register_file_ff.sv
../rtl/ibex_register_file_fpga.sv
../rtl/ibex_register_file_latch.sv
../rtl/ibex_pmp.sv
../rtl/ibex_core.sv
../rtl/ibex_trvk.sv
../rtl/ibex_top.sv
