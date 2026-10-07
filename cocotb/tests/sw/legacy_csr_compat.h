// Copyright lowRISC contributors.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// Legacy CSR aliases used by the vendored riscv-tests that modern binutils
// no longer recognises. The UVM CI builds these tests with an older
// toolchain (ci/vars.env, 20220210-1) that still accepts the draft-1.9
// names; the xpack toolchain used by this environment does not, so the
// names are mapped to their ratified equivalents here. The vendored test
// sources stay untouched.
#ifndef IBEX_COCOTB_LEGACY_CSR_COMPAT_H
#define IBEX_COCOTB_LEGACY_CSR_COMPAT_H

#define sptbr    satp
#define mbadaddr mtval
#define sbadaddr stval

#endif // IBEX_COCOTB_LEGACY_CSR_COMPAT_H
