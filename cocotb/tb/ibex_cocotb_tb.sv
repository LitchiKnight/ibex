// Copyright lowRISC contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// Cocotb testbench top for the Ibex core (part of the cocotb/ verification
// environment, see cocotb/PLAN.md).
//
// This is a thin SV layer: it instantiates `ibex_top` and exposes
//  - the instruction/data memory interfaces as flat ports, driven by Python,
//  - the RVFI outputs, monitored by Python,
//  - a small command interface through which Python drives the SpikeCosim
//    comparison core (dv/cosim) via its existing DPI functions. The DPI
//    functions are called from SV because Verilator's VPI does not expose DPI
//    objects to cocotb directly.

`include "spike_cosim_dpi.svh"
`include "cosim_dpi.svh"
// Command opcodes and packed-argument bit positions, generated from the
// single layout definition py/cmd_defs.py (see gen/cmd_defs.py); the
// Python side imports the same numbers directly, so the two copies can
// never drift. The CMD_INIT layout fingerprint remains as a runtime
// sanity check on top.
`include "cmd_defs.svh"

module ibex_cocotb_tb import ibex_pkg::*; import ibex_cheriot_pkg::*; #(
  // Cosim configuration. Only the values that cannot be derived from the DUT
  // parameters below live here: the Spike ISA string and the UVM boot
  // convention (BOOT_ADDR = 32'h8000_0000, the reset vector is
  // {BOOT_ADDR[31:8], 8'h80} and the initial mtvec is {BOOT_ADDR[31:8],
  // 8'h01}). Everything else is derived from the DUT parameters, so the
  // co-simulator and the DUT can never silently diverge.
  parameter string        ISA_STRING  = "rv32imc",
  parameter int unsigned  START_PC    = 32'h8000_0080,
  parameter int unsigned  START_MTVEC = 32'h8000_0001,

  // Ibex DUT parameters.
  parameter base_isa_e    BaseIsa                  = BaseIsaRV32I,
  parameter bit           PMPEnable                = 1'b1,
  parameter int unsigned  PMPGranularity           = 0,
  parameter int unsigned  PMPNumRegions            = 4,
  parameter int unsigned  MHPMCounterNum           = 0,
  parameter int unsigned  MHPMCounterWidth         = 40,
  parameter bit           RV32E                    = 1'b0,
  parameter rv32m_e       RV32M                    = RV32MFast,
  parameter rv32b_e       RV32B                    = RV32BNone,
  parameter rv32zc_e      RV32ZC                   = RV32ZcaZcbZcmp,
  parameter regfile_e     RegFile                  = RegFileFF,
  parameter bit           BranchTargetALU          = 1'b0,
  parameter bit           WritebackStage           = 1'b0,
  parameter bit           ICache                   = 1'b0,
  parameter bit           ICacheECC                = 1'b0,
  parameter bit           ICacheTweakInfection     = 1'b0,
  parameter bit           BranchPredictor          = 1'b0,
  parameter bit           DbgTriggerEn             = 1'b1,
  parameter int unsigned  DbgHwBreakNum            = 1,
  parameter bit           SecureIbex               = 1'b0,
  parameter int unsigned  LockstepOffset           = 1,
  parameter bit           MemECC                   = SecureIbex,
  parameter int unsigned  MemDataWidth             = MemECC ? 32 + 7 : 32,
  parameter bit           ICacheScramble           = 1'b0,
  parameter lfsr_seed_t   RndCnstLfsrSeed          = RndCnstLfsrSeedDefault,
  parameter lfsr_perm_t   RndCnstLfsrPerm          = RndCnstLfsrPermDefault,
  parameter int unsigned  DmBaseAddr               = 32'h1A110000,
  parameter int unsigned  DmAddrMask               = 32'h00000FFF,
  parameter int unsigned  DmHaltAddr               = 32'h1A110800,
  parameter int unsigned  DmExceptionAddr          = 32'h1A110808,
  parameter int unsigned  CheriotRevBitmapAddrWidth = 32'd11,
  parameter int unsigned  CheriotRevBitmapBaseAddr  = 32'h0,
  // DUT boot address. The Makefile passes the same value through the
  // +ibex_cocotb_load_addr plusarg, which is the runtime source for the image
  // load base (Python memory model and co-simulator backdoor write).
  parameter int unsigned  BootAddr                 = 32'h8000_0000
) (
  // The tb generates its own clock and exposes it as an output so the
  // Python side can use it as its timebase. A Verilator-specific note: the
  // VPI-driven clock does not interact reliably with the level-sensitive
  // clock-gate latch inside ibex_top, so the clock is generated in SV.
  output logic        clk_o,
  input  logic        rst_ni,

  // Instruction memory interface (slave side, driven by Python).
  output logic        instr_req_o,
  input  logic        instr_gnt_i,
  input  logic        instr_rvalid_i,
  output logic [31:0] instr_addr_o,
  input  logic [31:0] instr_rdata_i,
  input  logic [6:0]  instr_rdata_intg_i,
  input  logic        instr_err_i,

  // Data memory interface (slave side, driven by Python).
  output logic        data_req_o,
  input  logic        data_gnt_i,
  input  logic        data_rvalid_i,
  output logic        data_we_o,
  output logic [3:0]  data_be_o,
  output logic [31:0] data_addr_o,
  output logic [31:0] data_wdata_o,
  input  logic [31:0] data_rdata_i,
  input  logic [6:0]  data_rdata_intg_i,
  input  logic        data_err_i,

  // Interrupt inputs.
  input  logic        irq_software_i,
  input  logic        irq_timer_i,
  input  logic        irq_external_i,
  input  logic [14:0] irq_fast_i,
  input  logic        irq_nm_i,

  // Debug request.
  input  logic        debug_req_i,

  // Alerts and fault status (monitored by Python).
  output logic        alert_minor_o,
  output logic        alert_major_internal_o,
  output logic        alert_major_bus_o,
  output logic        double_fault_seen_o,

  // Crash dump captured on the cycle a trap retires on RVFI. Lets the
  // Python side report exception_pc/exception_addr (mtval) for cosim
  // mismatch debugging; the value is only meaningful after rvfi_trap.
  output logic [159:0] trap_crash_dump,

  // Constant copy of the SecureIbex DUT parameter. The memory agent uses it
  // to apply the same rule as the UVM base test: spurious responses are
  // only allowed on secure configurations, whose core gates stray bus
  // responses (ibex_core.sv g_check_mem_response); with SecureIbex=0 the
  // core trusts the bus protocol and a spurious response would corrupt it.
  output logic        secure_ibex_o,

  // Data access classification, sampled by Python at the request phase and
  // forwarded to the co-simulator. Derived from the same LSU internals the
  // UVM tb probes hierarchically (core_ibex_tb_top.sv): the bus-level
  // signals alone cannot distinguish the halves of a misaligned access
  // from aligned byte/halfword accesses.
  output logic        data_misaligned_first_o,
  output logic        data_misaligned_second_o,
  output logic        data_misaligned_first_saw_error_o,
  output logic        data_m_mode_o,

`ifdef RVFI
  // RISC-V Formal Interface outputs (monitored by Python).
  output logic        rvfi_valid,
  output logic [63:0] rvfi_order,
  output logic [31:0] rvfi_insn,
  output logic        rvfi_trap,
  output logic        rvfi_halt,
  output logic        rvfi_intr,
  output logic [ 1:0] rvfi_mode,
  output logic [ 1:0] rvfi_ixl,
  output logic [ 4:0] rvfi_rs1_addr,
  output logic [ 4:0] rvfi_rs2_addr,
  output logic [ 4:0] rvfi_rs3_addr,
  output logic [31:0] rvfi_rs1_rdata,
  output logic [31:0] rvfi_rs2_rdata,
  output logic [31:0] rvfi_rs3_rdata,
  output logic [ 4:0] rvfi_rd_addr,
  output logic [31:0] rvfi_rd_wdata,
  output logic [31:0] rvfi_pc_rdata,
  output logic [31:0] rvfi_pc_wdata,
  output logic [31:0] rvfi_mem_addr,
  output logic [ 3:0] rvfi_mem_rmask,
  output logic [ 3:0] rvfi_mem_wmask,
  output logic [31:0] rvfi_mem_rdata,
  output logic [31:0] rvfi_mem_wdata,
  output logic [31:0] rvfi_ext_pre_mip,
  output logic [31:0] rvfi_ext_post_mip,
  output logic        rvfi_ext_nmi,
  output logic        rvfi_ext_nmi_int,
  output logic        rvfi_ext_debug_req,
  output logic        rvfi_ext_debug_mode,
  output logic        rvfi_ext_rf_wr_suppress,
  output logic [63:0] rvfi_ext_mcycle,
  output logic [31:0] rvfi_ext_mhpmcounters [10],
  output logic [31:0] rvfi_ext_mhpmcountersh [10],
  output logic        rvfi_ext_ic_scr_key_valid,
  output logic        rvfi_ext_irq_valid,
  // Expanded form of the retired instruction. rvfi_insn presents
  // unexpanded compressed instructions as {16'b0, 16-bit encoding}; the
  // monitor uses this field instead whenever it is valid (the same choice
  // as the UVM ibex_rvfi_monitor), so the coverage classifier always sees
  // the 32-bit uncompressed encoding.
  output logic        rvfi_ext_expanded_insn_valid,
  output logic [31:0] rvfi_ext_expanded_insn,
  output logic        rvfi_ext_expanded_insn_last,
`endif

  // SpikeCosim command interface (driven by Python).
  input  logic        cmd_valid,
  input  logic [ 7:0] cmd_op,
  input  logic [31:0] cmd_a0,
  input  logic [31:0] cmd_a1,
  input  logic [31:0] cmd_a2,
  input  logic [31:0] cmd_a3,
  input  logic [31:0] cmd_a4,
  input  logic [31:0] cmd_a5,
  input  logic [31:0] cmd_a6,
  output logic        cmd_ack,
  output logic [31:0] cmd_ret0
);

`ifndef RVFI
  $fatal("Fatal error: RVFI needs to be defined globally.");
`endif

  // FNV-1a (32-bit) over the command opcodes and the packed-argument bit
  // positions. Both sides derive their numbers from the single layout
  // definition (py/cmd_defs.py, rendered into the cmd_defs.svh include
  // above), so this fingerprint is a runtime sanity check rather than a
  // drift detector; CMD_INIT returns it so the Python side verifies the
  // two copies agree. The result is forced odd so it can never collide
  // with the 0 init-failure return value.
  function automatic int unsigned layout_fingerprint();
    int unsigned h = 32'h811c0001;
    int unsigned values[22] = '{
      32'(`CMD_INIT), 32'(`CMD_STEP), 32'(`CMD_GET_ERRORS),
      32'(`CMD_GET_INSN_CNT), 32'(`CMD_RELEASE), 32'(`CMD_NOTIFY_DSIDE),
      32'(`CMD_SET_MIP), 32'(`CMD_GET_ERROR_STR),
      `STEP_RD_ADDR_LSB, `STEP_RF_WR_SUPPRESS_BIT, `STEP_TRAP_BIT,
      `STEP_NMI_INT_BIT, `STEP_NMI_BIT, `STEP_DEBUG_REQ_BIT,
      `DSIDE_STORE_BIT, `DSIDE_ERROR_BIT, `DSIDE_MIS_FIRST_BIT,
      `DSIDE_MIS_SECOND_BIT, `DSIDE_MIS_FIRST_ERR_BIT, `DSIDE_M_MODE_BIT,
      `SETMIP_NMI_BIT, `SETMIP_NMI_INT_BIT
    };
    for (int i = 0; i < $size(values); i++) begin
      h = (h ^ values[i]) * 32'h01000193;
    end
    return h | 32'h1;
  endfunction

  chandle     cosim_handle = null;
  string      bin_path     = "";
  int unsigned load_addr;

  // DPI helper declarations (defined in cocotb/dpi/ibex_cocotb_dpi.cc).
  import "DPI-C" function chandle ibex_cocotb_cosim_init(
    string isa_string, int unsigned start_pc, int unsigned start_mtvec,
    string bin_path, int unsigned load_addr, int unsigned pmp_num_regions,
    int unsigned pmp_granularity, int unsigned mhpm_counter_num,
    bit secure_ibex, bit icache, int unsigned dm_start_addr,
    int unsigned dm_end_addr);
  import "DPI-C" function int ibex_cocotb_drain_errors(chandle cosim_handle);
  import "DPI-C" function int unsigned ibex_cocotb_get_insn_cnt(chandle cosim_handle);
  import "DPI-C" function int ibex_cocotb_get_error_str(chandle cosim_handle,
                                                        int index, int word);

  logic clk;
  crash_dump_t crash_dump;

  initial begin
    clk = 1'b0;
    forever #5ns clk = ~clk;
  end

  assign clk_o = clk;

  assign secure_ibex_o = SecureIbex;

  // The same hierarchical derivations as the UVM tb (core_ibex_tb_top.sv),
  // probing the LSU internals, latched at the address phase: the UVM
  // monitor samples these signals on the (request && grant) cycle, which
  // is also when their values are architecturally meaningful. Latching
  // them here keeps the Python side independent of its sampling skew.
  logic data_misaligned_first_probe;
  logic data_misaligned_second_probe;
  logic data_misaligned_first_saw_error_probe;
  logic data_m_mode_probe;

  assign data_misaligned_first_probe =
    u_ibex_top.u_ibex_core.load_store_unit_i.handle_misaligned_d |
    ((u_ibex_top.u_ibex_core.load_store_unit_i.lsu_type_i == 2'b01) &
     (u_ibex_top.u_ibex_core.load_store_unit_i.data_offset == 2'b01));
  assign data_misaligned_second_probe =
    u_ibex_top.u_ibex_core.load_store_unit_i.addr_incr_req_o;
  assign data_misaligned_first_saw_error_probe =
    u_ibex_top.u_ibex_core.load_store_unit_i.addr_incr_req_o &
    u_ibex_top.u_ibex_core.load_store_unit_i.lsu_err_d;
  assign data_m_mode_probe =
    u_ibex_top.u_ibex_core.priv_mode_lsu == ibex_pkg::PRIV_LVL_M;

  always_ff @(posedge clk or negedge rst_ni) begin
    if (!rst_ni) begin
      data_misaligned_first_o           <= 1'b0;
      data_misaligned_second_o          <= 1'b0;
      data_misaligned_first_saw_error_o <= 1'b0;
      data_m_mode_o                     <= 1'b0;
    end else if (data_req_o && data_gnt_i) begin
      data_misaligned_first_o           <= data_misaligned_first_probe;
      data_misaligned_second_o          <= data_misaligned_second_probe;
      data_misaligned_first_saw_error_o <= data_misaligned_first_saw_error_probe;
      data_m_mode_o                     <= data_m_mode_probe;
    end
  end

`ifdef RVFI
  // Hold the core's crash dump while a trap retires and clear it while any
  // other instruction retires, for Python-side inspection (exception_pc =
  // mepc, exception_addr = mtval). The monitor samples this register with
  // every RVFI item, so the value is meaningful exactly when rvfi_trap is
  // set on the sampled item - never stale from an earlier trap.
  always_ff @(posedge clk or negedge rst_ni) begin
    if (!rst_ni) begin
      trap_crash_dump <= '0;
    end else if (rvfi_valid) begin
      trap_crash_dump <= rvfi_trap ? crash_dump : '0;
    end
  end
`endif

  // Execute one command per request; ack is held until Python drops cmd_valid.
  always_ff @(posedge clk or negedge rst_ni) begin
    if (!rst_ni) begin
      cmd_ack  <= 1'b0;
      cmd_ret0 <= '0;
    end else if (cmd_valid && !cmd_ack) begin
      cmd_ack <= 1'b1;
      if ((cosim_handle == null) && (cmd_op != `CMD_INIT)) begin
        // A command without a live co-simulator means it was issued before
        // CMD_INIT or after CMD_RELEASE; fail loudly instead of
        // dereferencing null in the DPI helpers.
        $error("ibex_cocotb: cmd op 0x%0x issued before CMD_INIT or after CMD_RELEASE", cmd_op);
        cmd_ret0 <= 32'h0;
      end else begin
        unique case (cmd_op)
          `CMD_INIT: begin
            void'($value$plusargs("ibex_cocotb_bin=%s", bin_path));
            if (!$value$plusargs("ibex_cocotb_load_addr=%h", load_addr)) begin
              load_addr = BootAddr;
            end
            cosim_handle = ibex_cocotb_cosim_init(
              ISA_STRING, START_PC, START_MTVEC, bin_path, load_addr,
              PMPNumRegions, PMPGranularity, MHPMCounterNum,
              SecureIbex, ICache, DmBaseAddr, DmBaseAddr + DmAddrMask + 1);
            // Success carries the layout fingerprint so the Python side can
            // verify the two copies of the protocol agree; 0 is failure.
            cmd_ret0 <= (cosim_handle != null) ? layout_fingerprint()
                                               : 32'h0;
          end
          `CMD_STEP: begin
            // Same call order as the UVM cosim scoreboard: debug_req, NMI,
            // internal NMI, MIP, mcycle, then step.
            riscv_cosim_set_debug_req(cosim_handle, cmd_a2[`STEP_DEBUG_REQ_BIT]);
            riscv_cosim_set_nmi(cosim_handle, cmd_a2[`STEP_NMI_BIT]);
            riscv_cosim_set_nmi_int(cosim_handle, cmd_a2[`STEP_NMI_INT_BIT]);
            riscv_cosim_set_mip(cosim_handle, cmd_a3, cmd_a4);
            riscv_cosim_set_mcycle(cosim_handle, {cmd_a6, cmd_a5});
            cmd_ret0 <= riscv_cosim_step(cosim_handle,
                                         cmd_a2[4:`STEP_RD_ADDR_LSB], cmd_a1,
                                         cmd_a0, cmd_a2[`STEP_TRAP_BIT],
                                         cmd_a2[`STEP_RF_WR_SUPPRESS_BIT]);
          end
          `CMD_GET_ERRORS: begin
            cmd_ret0 <= ibex_cocotb_drain_errors(cosim_handle);
          end
          `CMD_GET_INSN_CNT: begin
            cmd_ret0 <= ibex_cocotb_get_insn_cnt(cosim_handle);
          end
          `CMD_RELEASE: begin
            spike_cosim_release(cosim_handle);
            cosim_handle = null;
            cmd_ret0 <= 32'h1;
          end
          `CMD_NOTIFY_DSIDE: begin
            riscv_cosim_notify_dside_access(cosim_handle,
              cmd_a3[`DSIDE_STORE_BIT], cmd_a0, cmd_a1, cmd_a2[3:0],
              cmd_a3[`DSIDE_ERROR_BIT], cmd_a3[`DSIDE_MIS_FIRST_BIT],
              cmd_a3[`DSIDE_MIS_SECOND_BIT], cmd_a3[`DSIDE_MIS_FIRST_ERR_BIT],
              cmd_a3[`DSIDE_M_MODE_BIT]);
            cmd_ret0 <= 32'h1;
          end
          `CMD_SET_MIP: begin
            // IRQ-only RVFI event: same call order as the UVM scoreboard's
            // irq_only branch (set_nmi, set_nmi_int, set_mip(pre, pre), no
            // step).
            riscv_cosim_set_nmi(cosim_handle, cmd_a2[`SETMIP_NMI_BIT]);
            riscv_cosim_set_nmi_int(cosim_handle, cmd_a2[`SETMIP_NMI_INT_BIT]);
            riscv_cosim_set_mip(cosim_handle, cmd_a3, cmd_a3);
            cmd_ret0 <= 32'h1;
          end
          `CMD_GET_ERROR_STR: begin
            // Indexed comparison error string, transferred 32 bits at a
            // time: a0 is the error index, a1 the word index (byte i at
            // bits [8*(i%4)+7:8*(i%4)] of word i/4), or 0xFFFFFFFF for the
            // string length. An out-of-range index returns 0; the error
            // list is left untouched.
            cmd_ret0 <= ibex_cocotb_get_error_str(cosim_handle, cmd_a0,
                                                  cmd_a1);
          end
          default: begin
            $error("ibex_cocotb: unknown cmd op 0x%0x", cmd_op);
            cmd_ret0 <= 32'h0;
          end
        endcase
      end
    end else if (!cmd_valid && cmd_ack) begin
      cmd_ack <= 1'b0;
    end
  end

  ibex_top #(
    .BaseIsa                  (BaseIsa),
    .PMPEnable                (PMPEnable),
    .PMPGranularity           (PMPGranularity),
    .PMPNumRegions            (PMPNumRegions),
    .MHPMCounterNum           (MHPMCounterNum),
    .MHPMCounterWidth         (MHPMCounterWidth),
    .RV32E                    (RV32E),
    .RV32M                    (RV32M),
    .RV32B                    (RV32B),
    .RV32ZC                   (RV32ZC),
    .RegFile                  (RegFile),
    .BranchTargetALU          (BranchTargetALU),
    .WritebackStage           (WritebackStage),
    .ICache                   (ICache),
    .ICacheECC                (ICacheECC),
    .ICacheTweakInfection     (ICacheTweakInfection),
    .BranchPredictor          (BranchPredictor),
    .DbgTriggerEn             (DbgTriggerEn),
    .DbgHwBreakNum            (DbgHwBreakNum),
    .SecureIbex               (SecureIbex),
    .LockstepOffset           (LockstepOffset),
    .MemECC                   (MemECC),
    .MemDataWidth             (MemDataWidth),
    .ICacheScramble           (ICacheScramble),
    .RndCnstLfsrSeed          (RndCnstLfsrSeed),
    .RndCnstLfsrPerm          (RndCnstLfsrPerm),
    .DmBaseAddr               (DmBaseAddr),
    .DmAddrMask               (DmAddrMask),
    .DmHaltAddr               (DmHaltAddr),
    .DmExceptionAddr          (DmExceptionAddr),
    .CheriotRevBitmapAddrWidth(CheriotRevBitmapAddrWidth),
    .CheriotRevBitmapBaseAddr (CheriotRevBitmapBaseAddr)
  ) u_ibex_top (
    .clk_i(clk),
    .rst_ni,

    // Enable all clock gates for testing: the Verilator VPI-driven clock
    // does not reliably drive the level-sensitive gate latch, so the core
    // clock would otherwise never start.
    .test_en_i                 (1'b1),
    .scan_rst_ni               (1'b1),
    .ram_cfg_icache_tag_i      ('{default: prim_ram_1p_pkg::RAM_1P_CFG_REQ_DEFAULT}),
    .ram_cfg_icache_tag_o      (),
    .ram_cfg_icache_data_i     ('{default: prim_ram_1p_pkg::RAM_1P_CFG_REQ_DEFAULT}),
    .ram_cfg_icache_data_o     (),

    .cheriot_enable_i          (IbexMuBiOff),
    .hart_id_i                 (32'b0),
    .boot_addr_i               (BootAddr),

    .instr_req_o,
    .instr_gnt_i,
    .instr_rvalid_i,
    .instr_addr_o,
    .instr_rdata_i,
    .instr_rdata_intg_i,
    .instr_err_i,

    .data_req_o,
    .data_gnt_i,
    .data_rvalid_i,
    .data_we_o,
    .data_be_o,
    .data_addr_o,
    .data_wdata_o,
    .data_wdata_intg_o        (),
    .data_rdata_i,
    .data_rdata_intg_i,
    .data_tag_i                (1'b0),
    .data_tag_o                (),
    .data_err_i,

    .trvk_heap_base_addr_i     (32'b0),
    .trvk_revbm_req_o          (),
    .trvk_revbm_gnt_i          (1'b0),
    .trvk_revbm_rvalid_i       (1'b0),
    .trvk_revbm_addr_o         (),
    .trvk_revbm_rdata_i        (32'b0),
    .trvk_revbm_rdata_intg_i   (7'b0),
    .trvk_revbm_err_i          (1'b0),

    .irq_software_i,
    .irq_timer_i,
    .irq_external_i,
    .irq_fast_i,
    .irq_nm_i,

    .scramble_key_valid_i      (1'b0),
    .scramble_key_i            ('0),
    .scramble_nonce_i          ('0),
    .scramble_req_o            (),

    .debug_req_i,
    .crash_dump_o              (crash_dump),
    .double_fault_seen_o,

`ifdef RVFI
    .rvfi_valid,
    .rvfi_order,
    .rvfi_insn,
    .rvfi_trap,
    .rvfi_halt,
    .rvfi_intr,
    .rvfi_mode,
    .rvfi_ixl,
    .rvfi_rs1_addr,
    .rvfi_rs2_addr,
    .rvfi_rs3_addr,
    .rvfi_rs1_rdata,
    .rvfi_rs1_rcap             (),
    .rvfi_rs2_rdata,
    .rvfi_rs2_rcap             (),
    .rvfi_rs3_rdata,
    .rvfi_rd_addr,
    .rvfi_rd_wdata,
    .rvfi_rd_wcap              (),
    .rvfi_pc_rdata,
    .rvfi_pc_wdata,
    .rvfi_mem_addr,
    .rvfi_mem_rmask,
    .rvfi_mem_wmask,
    .rvfi_mem_rdata,
    .rvfi_mem_wdata,
    .rvfi_mem_rcap             (),
    .rvfi_mem_wcap             (),
    .rvfi_mem_is_cap           (),
    .rvfi_ext_pre_mip,
    .rvfi_ext_post_mip,
    .rvfi_ext_nmi,
    .rvfi_ext_nmi_int,
    .rvfi_ext_debug_req,
    .rvfi_ext_debug_mode,
    .rvfi_ext_rf_wr_suppress,
    .rvfi_ext_mcycle,
    .rvfi_ext_mhpmcounters,
    .rvfi_ext_mhpmcountersh,
    .rvfi_ext_ic_scr_key_valid,
    .rvfi_ext_irq_valid,
    .rvfi_ext_expanded_insn_valid,
    .rvfi_ext_expanded_insn,
    .rvfi_ext_expanded_insn_last,
`endif

    .fetch_enable_i            (IbexMuBiOn),
    .mcounteren_writable_i     (IbexMuBiOn),
    .alert_minor_o,
    .alert_major_internal_o,
    .alert_major_bus_o,
    .core_sleep_o              (),

    .lockstep_cmp_en_o         (),

    .data_req_shadow_o         (),
    .data_we_shadow_o          (),
    .data_be_shadow_o          (),
    .data_addr_shadow_o        (),
    .data_wdata_shadow_o       (),
    .data_wdata_intg_shadow_o  (),

    .instr_req_shadow_o        (),
    .instr_addr_shadow_o       ()
  );

endmodule
