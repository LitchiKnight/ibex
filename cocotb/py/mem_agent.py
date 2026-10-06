# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Memory agent for the cocotb verification environment.

M1 milestone: a minimal single-slave memory model serving both the
instruction and the data interfaces with fixed timing (grant one cycle
after the request, response one cycle after the grant). Randomised
delays, error injection and spurious responses (the M2 features mirroring
``ibex_mem_intf_response_agent``) are intentionally not implemented yet.

The memory is page-backed and sparse, so any address can be accessed (reads
of never-written pages return zero, which is a legal value rather than an
error). Writes to the watched handshake addresses (signature/tohost) are
recorded and wake up the test; interpreting those writes as a pass/fail
outcome is the test's job, see ``classify_test_result`` below.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import cocotb
from cocotb.triggers import Event, RisingEdge

logger = logging.getLogger("cocotb.mem_agent")

_PAGE_BITS = 12
_PAGE_SIZE = 1 << _PAGE_BITS
_PAGE_MASK = _PAGE_SIZE - 1


@dataclass(frozen=True)
class DsideAccess:
    """One data-side access, reported to the co-simulator once its response
    has been driven (mirrors ``DSideAccessInfo`` in dv/cosim/cosim.h)."""

    store: bool
    addr: int
    # Store data, or the load data returned to the DUT.
    data: int
    be: int
    error: bool = False
    misaligned_first: bool = False
    misaligned_second: bool = False
    misaligned_first_saw_error: bool = False
    m_mode_access: bool = True


def classify_test_result(writes, signature_addr: int, tohost_addr: int):
    """Interpret the recorded handshake writes as a test outcome.

    The ibex-patched riscv-test-env reports a TEST_RESULT write to the
    signature address (value[7:0] == 1, value[8] == 0 means pass); failures
    and exceptions also write an odd value to the tohost section. Returns
    ``"pass"``, ``"fail"`` or ``None`` when the test has not reported yet.
    """
    for addr, data in writes:
        if addr == signature_addr and (data & 0xFF) == 1:
            return "pass" if (data & 0x100) == 0 else "fail"
        if addr == tohost_addr:
            return "pass" if (data & 1) == 0 else "fail"
    return None


class IbexMemAgent:
    """Page-backed sparse memory for the instruction and data interfaces.

    Invariants: every granted request gets exactly one one-cycle response;
    data-side accesses are forwarded to ``on_access`` once their response
    has been driven.
    """

    def __init__(self, dut, tohost_addr: int, signature_addr: int):
        self.dut = dut
        self.tohost_addr = tohost_addr
        self.signature_addr = signature_addr
        self.pages = {}
        # [(addr, data)] writes to the watched handshake addresses, in order.
        self.observed_writes = []
        self.done_event = Event()
        self.load_count = 0
        self.store_count = 0
        # Optional coroutine callback invoked with a DsideAccess once its
        # response has been driven; the scoreboard uses it to notify the
        # co-simulator.
        self.on_access = None

    def load_bin(self, path: Path, base: int):
        data = path.read_bytes()
        for offset, byte in enumerate(data):
            self._write_byte(base + offset, byte)
        logger.info("loaded %d bytes from %s at 0x%08x", len(data), path, base)

    def _page(self, addr: int):
        return self.pages.get(addr >> _PAGE_BITS)

    def _page_for_write(self, addr: int):
        key = addr >> _PAGE_BITS
        page = self.pages.get(key)
        if page is None:
            page = bytearray(_PAGE_SIZE)
            self.pages[key] = page
        return page

    def _read_byte(self, addr: int) -> int:
        page = self._page(addr)
        return page[addr & _PAGE_MASK] if page is not None else 0

    def _write_byte(self, addr: int, value: int):
        self._page_for_write(addr)[addr & _PAGE_MASK] = value & 0xFF

    def read_word(self, addr: int) -> int:
        return int.from_bytes(
            bytes(self._read_byte(addr + i) for i in range(4)), "little"
        )

    def write_word(self, addr: int, data: int, be: int):
        for i in range(4):
            if be & (1 << i):
                self._write_byte(addr + i, (data >> (8 * i)) & 0xFF)
        if addr in (self.signature_addr, self.tohost_addr):
            self.observed_writes.append((addr, data))
            self.done_event.set()

    async def _complete_response(self, gnt, rvalid, rdata, response_data):
        # Grant one cycle after the request, respond one cycle after the
        # grant. rvalid must stay a strict one-cycle pulse: holding it high
        # while the notify handshake runs would keep the LSU's load-complete
        # signal asserted and clobber the register-file write of the next
        # instruction.
        gnt.value = 1
        await RisingEdge(self.dut.clk_o)
        gnt.value = 0

        rdata.value = response_data
        rvalid.value = 1
        await RisingEdge(self.dut.clk_o)
        rvalid.value = 0

    async def _serve_instr_port(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            if not int(self.dut.instr_req_o.value):
                continue
            request_addr = int(self.dut.instr_addr_o.value)
            await self._complete_response(
                self.dut.instr_gnt_i, self.dut.instr_rvalid_i,
                self.dut.instr_rdata_i, self.read_word(request_addr))

    async def _serve_data_port(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            if not int(self.dut.data_req_o.value):
                continue

            # Capture the request phase; the outputs deassert in the grant
            # cycle, so everything must be read here.
            request_addr = int(self.dut.data_addr_o.value)
            if int(self.dut.data_we_o.value):
                request_data = int(self.dut.data_wdata_o.value)
                request_be = int(self.dut.data_be_o.value)
                # M1 commits the store at the request phase: there are no
                # spurious responses or back-pressure, so a granted request
                # always completes. M2 error injection must move this to the
                # response phase.
                self.write_word(request_addr, request_data, request_be)
                self.store_count += 1
                response_data = 0
                access = DsideAccess(store=True, addr=request_addr,
                                     data=request_data, be=request_be)
            else:
                self.load_count += 1
                response_data = self.read_word(request_addr)
                # For loads the DUT drives the byte enables of the access
                # size (e.g. 2'b11 for a halfword), which the co-simulator
                # checks against its own access.
                access = DsideAccess(store=False, addr=request_addr,
                                     data=response_data,
                                     be=int(self.dut.data_be_o.value))

            await self._complete_response(
                self.dut.data_gnt_i, self.dut.data_rvalid_i,
                self.dut.data_rdata_i, response_data)

            # The co-simulator contract requires accesses to be notified once
            # their response is seen; the notify handshake runs while the bus
            # is idle.
            if self.on_access is not None:
                await self.on_access(access)

    async def _supervise(self, name, coro):
        try:
            await coro
        except Exception:
            logger.exception("mem agent: %s port died", name)
            raise

    async def run(self):
        # Both loops run forever; the awaits keep run() alive so a failure
        # in either task (re-raised by _supervise) is reported by cocotb.
        instr_task = cocotb.start_soon(
            self._supervise("instr", self._serve_instr_port()))
        data_task = cocotb.start_soon(
            self._supervise("data", self._serve_data_port()))
        await instr_task
        await data_task
