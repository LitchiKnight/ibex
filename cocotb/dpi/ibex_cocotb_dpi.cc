// Copyright lowRISC contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// Convenience DPI entry points for the cocotb verification environment
// (cocotb/, see cocotb/PLAN.md).
//
// The SpikeCosim comparison core itself lives in dv/cosim and is compiled
// into the simulation unchanged. This file only adds thin helpers for
// initialisation with a binary image and for error reporting; it must not
// reimplement any checking logic.

#include <svdpi.h>

#include <cstdio>
#include <vector>

#include "cosim.h"

// Defined in dv/uvm/core_ibex/common/ibex_cosim_agent/spike_cosim_dpi.cc,
// which is compiled into the simulation unchanged.
extern "C" void *spike_cosim_init(const char *isa_string, svBitVecVal *start_pc,
                                  svBitVecVal *start_mtvec,
                                  const char *log_file_path_cstr,
                                  svBitVecVal *pmp_num_regions,
                                  svBitVecVal *pmp_granularity,
                                  svBitVecVal *mhpm_counter_num,
                                  svBit secure_ibex, svBit icache,
                                  svBitVecVal *dm_start_addr,
                                  svBitVecVal *dm_end_addr);

extern "C" void spike_cosim_release(void *cosim_handle);

// Read a whole binary file; returns false (after reporting why) on failure.
static bool read_file(const char *path, std::vector<uint8_t> *data) {
  std::FILE *file = std::fopen(path, "rb");
  if (!file) {
    std::fprintf(stderr, "ibex_cocotb: cannot open binary %s\n", path);
    return false;
  }

  std::fseek(file, 0, SEEK_END);
  long size = std::ftell(file);
  std::fseek(file, 0, SEEK_SET);
  if (size <= 0) {
    std::fprintf(stderr, "ibex_cocotb: binary %s is empty\n", path);
    std::fclose(file);
    return false;
  }

  data->resize(static_cast<size_t>(size));
  if (std::fread(data->data(), 1, data->size(), file) != data->size()) {
    std::fprintf(stderr, "ibex_cocotb: failed to read binary %s\n", path);
    std::fclose(file);
    return false;
  }
  std::fclose(file);
  return true;
}

extern "C" {

void *ibex_cocotb_cosim_init(const char *isa_string, unsigned int start_pc,
                             unsigned int start_mtvec, const char *bin_path,
                             unsigned int load_addr,
                             unsigned int pmp_num_regions,
                             unsigned int pmp_granularity,
                             unsigned int mhpm_counter_num,
                             unsigned char secure_ibex, unsigned char icache,
                             unsigned int dm_start_addr,
                             unsigned int dm_end_addr) {
  svBitVecVal start_pc_val[1] = {{start_pc}};
  svBitVecVal start_mtvec_val[1] = {{start_mtvec}};
  svBitVecVal pmp_num_regions_val[1] = {{pmp_num_regions}};
  svBitVecVal pmp_granularity_val[1] = {{pmp_granularity}};
  svBitVecVal mhpm_counter_num_val[1] = {{mhpm_counter_num}};
  svBitVecVal dm_start_addr_val[1] = {{dm_start_addr}};
  svBitVecVal dm_end_addr_val[1] = {{dm_end_addr}};

  Cosim *cosim = static_cast<Cosim *>(spike_cosim_init(
      isa_string, start_pc_val, start_mtvec_val, nullptr, pmp_num_regions_val,
      pmp_granularity_val, mhpm_counter_num_val, secure_ibex, icache,
      dm_start_addr_val, dm_end_addr_val));
  if (!cosim) {
    std::fprintf(stderr, "ibex_cocotb: spike_cosim_init failed\n");
    return nullptr;
  }

  if (bin_path && bin_path[0] != '\0') {
    // A configuration error here (wrong path, empty or unreadable file) must
    // fail the initialisation: continuing with an empty co-simulator memory
    // would turn it into a screenful of per-instruction mismatches.
    std::vector<uint8_t> data;
    if (!read_file(bin_path, &data)) {
      spike_cosim_release(cosim);
      return nullptr;
    }

    // Load the image into the co-simulator memory at the same base address
    // the Python memory model uses.
    if (!cosim->backdoor_write_mem(load_addr, data.size(), data.data())) {
      std::fprintf(stderr, "ibex_cocotb: backdoor write of %s failed\n",
                   bin_path);
      spike_cosim_release(cosim);
      return nullptr;
    }
    std::printf("ibex_cocotb: loaded %zu bytes from %s at 0x%08x\n",
                data.size(), bin_path, load_addr);
  }

  return cosim;
}

// Drain (print and clear) the comparison errors accumulated since the last
// call, returning the number of errors reported.
int ibex_cocotb_drain_errors(void *cosim_handle) {
  auto *cosim = static_cast<Cosim *>(cosim_handle);
  const auto &errors = cosim->get_errors();
  for (const auto &error : errors) {
    std::printf("ibex_cocotb cosim error: %s\n", error.c_str());
  }
  int num_errors = static_cast<int>(errors.size());
  cosim->clear_errors();
  return num_errors;
}

unsigned int ibex_cocotb_get_insn_cnt(void *cosim_handle) {
  return static_cast<Cosim *>(cosim_handle)->get_insn_cnt();
}

}  // extern "C"
