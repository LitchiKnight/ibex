# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Memory agent for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/ibex_mem_intf_agent``
(``ibex_mem_intf_response_agent_cfg`` + ``ibex_mem_intf_response_seq_lib``):

- grant/rvalid delays follow the same weighted distribution as the UVM agent
  (min heavily favoured, medium and slow rare); ``zero_delay_pct`` of runs
  pick zero delays everywhere, again as in the UVM config;
- spurious data-side responses are only driven while the data port is not
  serving a request, after a randomised inter-spurious delay, and only when
  the DUT is a secure configuration: with ``SecureIbex=0`` the core trusts
  the bus protocol and a stray response corrupts it, so tb_env forces the
  feature off exactly like the UVM base test (``g_check_mem_response`` in
  ibex_core.sv is the RTL gate). Spurious responses are never notified to
  the co-simulator;
- error injection: either permanent for addresses in ``error_addrs`` (used
  by the dedicated error test) or one-shot through ``inject_error()`` (the
  UVM ``error_synch`` knob, available to tests). Accesses to the handshake
  addresses never see an injected error. An errored store does not update
  the memory model, and an errored load returns random garbage; both are
  notified to the co-simulator with ``error`` set;
- stores commit at the response phase, not the request phase, so that a
  request granted but answered with an error never leaves a trace in memory.

The memory is page-backed and sparse, so any address can be accessed (reads
of never-written pages return zero, which is a legal value rather than an
error). The end-of-test handshake (which writes carry a result and what
they mean) lives entirely in ``TestHandshake``.
"""

import logging
import random
from dataclasses import dataclass
from pathlib import Path

import cocotb
from cocotb.triggers import Event, First, RisingEdge, Timer, gather

logger = logging.getLogger("cocotb.mem_agent")

_PAGE_BITS = 12
_PAGE_SIZE = 1 << _PAGE_BITS
_PAGE_MASK = _PAGE_SIZE - 1


@dataclass(frozen=True)
class DelayDist:
    """A randomised cycle-count distribution mirroring the UVM ``dist``
    constraints: the minimum is heavily favoured, the middle range gets a
    fixed weight and the maximum is rare. The buckets and weights are
    computed once at construction time."""

    minimum: int
    maximum: int
    fast_weight: int
    mid_weight: int
    medium_weight: int
    slow_weight: int

    def choices(self):
        if self.maximum <= self.minimum:
            return [self.minimum], [1]
        mid = (self.minimum + self.maximum) // 2
        if mid <= self.minimum:
            mid = self.minimum + 1
        buckets = [self.minimum, *range(self.minimum + 1, mid),
                   *range(mid, self.maximum), self.maximum]
        weights = ([self.fast_weight]
                   + [self.mid_weight] * max(0, mid - self.minimum - 1)
                   + [self.medium_weight] * max(0, self.maximum - mid)
                   + [self.slow_weight])
        return buckets, weights


# UVM response_agent_cfg distributions: grant favours min at 10:1:1, rvalid
# favours min at 5 with a fixed-weight middle band.
GNT_DELAY = DelayDist(minimum=0, maximum=10, fast_weight=10, mid_weight=3,
                      medium_weight=1, slow_weight=1)
VALID_DELAY = DelayDist(minimum=0, maximum=20, fast_weight=5, mid_weight=3,
                        medium_weight=1, slow_weight=1)


@dataclass(frozen=True)
class MemAgentConfig:
    """Randomisation knobs, semantically aligned with
    ``ibex_mem_intf_response_agent_cfg`` (UVM defaults preserved)."""

    # Delay distributions between request and grant / grant and rvalid.
    gnt_delay: DelayDist = GNT_DELAY
    valid_delay: DelayDist = VALID_DELAY
    # Percentage of runs where every delay is forced to zero (UVM
    # ``zero_delay_pct`` / ``zero_delays`` distribution).
    zero_delay_pct: int = 50
    # Spurious data-side responses. Only fired while the data port is not
    # serving a request, mirroring the UVM ``outstanding_accesses == 0``
    # condition.
    enable_spurious_response: bool = False
    spurious_response_delay_min: int = 0
    spurious_response_delay_max: int = 100
    # Permanent error injection: accesses to these addresses always receive
    # an error response (for the dedicated error test). One-shot injection
    # is available separately through ``inject_error()``.
    error_addrs: tuple = ()


@dataclass(frozen=True)
class TestHandshake:
    """The end-of-test handshake: which writes carry a result and what they
    mean. This is the single place that interprets the riscv-test-env
    conventions; the memory agent only records."""

    signature_addr: int
    tohost_addr: int

    def is_watched(self, addr: int) -> bool:
        return addr == self.signature_addr or addr == self.tohost_addr

    def classify(self, addr: int, data: int):
        """Interpret one watched write; returns ``"pass"``, ``"fail"`` or
        ``None`` when the write does not carry a result.

        The ibex-patched riscv-test-env reports a TEST_RESULT write to the
        signature address (value[7:0] == 1, value[8] == 0 means pass). The
        ecall tests (e.g. scall) pass through the trap vector instead, which
        writes TESTNUM to tohost; their pass value is exactly 1 (see
        rv64si/scall.S), while the failure path writes TESTNUM | 1337.
        """
        if addr == self.signature_addr and (data & 0xFF) == 1:
            return "pass" if (data & 0x100) == 0 else "fail"
        if addr == self.tohost_addr:
            return "pass" if data == 1 else "fail"
        return None


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


class IbexMemAgent:
    """Page-backed sparse memory for the instruction and data interfaces.

    Invariants: every granted request gets exactly one one-cycle response;
    data-side accesses are forwarded to ``on_access`` once their response
    has been driven (spurious responses are not, the co-simulator must never
    see them).
    """

    def __init__(self, dut, cfg: MemAgentConfig, handshake: TestHandshake,
                 on_access=None):
        self.dut = dut
        self.cfg = cfg
        self.handshake = handshake
        # Handshake/result addresses that must never see an injected error
        # (the UVM seq_lib protects its result addresses {0x8ffffff8,
        # 0x8ffffffc} for the same reason: an errored result write would
        # mask the test outcome; 0x8ffffffc is the UVM test_done handshake).
        self._no_error_addrs = {handshake.signature_addr,
                                handshake.tohost_addr,
                                handshake.signature_addr + 4}
        self._pages = {}
        # [(addr, data)] writes to the watched handshake addresses, in order.
        self.observed_writes = []
        self.done_event = Event()
        self.load_count = 0
        self.store_count = 0
        self.spurious_count = 0
        self.error_count = 0
        # Coroutine callback invoked with a DsideAccess once its response
        # has been driven; the scoreboard uses it to notify the
        # co-simulator.
        self.on_access = on_access
        # One-shot error injection (UVM error_synch: affects the very next
        # dside transaction, then clears).
        self._inject_error = False
        # True from the moment a data request is taken over until its
        # response pulse completes; the spurious-response task may not fire
        # while this is set.
        self._data_bus_busy = False
        # Chosen once per run, like the UVM zero_delays rand bit.
        self._zero_delays = (
            random.choices([True, False],
                           weights=[cfg.zero_delay_pct,
                                    100 - cfg.zero_delay_pct])[0])
        if self._zero_delays:
            logger.info("mem agent: zero delays selected for this run")
        self._gnt_choices = cfg.gnt_delay.choices()
        self._valid_choices = cfg.valid_delay.choices()

    # -- memory model -------------------------------------------------------

    def load_bin(self, path: Path, base: int):
        data = path.read_bytes()
        for offset, byte in enumerate(data):
            self._write_byte(base + offset, byte)
        logger.info("loaded %d bytes from %s at 0x%08x", len(data), path, base)

    def _page(self, addr: int):
        return self._pages.get(addr >> _PAGE_BITS)

    def _page_for_write(self, addr: int):
        key = addr >> _PAGE_BITS
        page = self._pages.get(key)
        if page is None:
            page = bytearray(_PAGE_SIZE)
            self._pages[key] = page
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

    # -- end-of-test handshake ------------------------------------------------

    async def wait_for_result(self, timeout_ns: int) -> str:
        """Block until the program reports a result through the handshake.

        ``done_event`` fires on every watched write, but only some of those
        writes carry a result (the first signature write is a CORE_STATUS
        marker), so keep waiting until classification succeeds or a full
        timeout elapses without any new write."""
        while True:
            self.done_event.clear()
            await First(self.done_event.wait(), Timer(timeout_ns, unit="ns"))
            for addr, data in self.observed_writes:
                result = self.handshake.classify(addr, data)
                if result is not None:
                    return result
            if not self.done_event.is_set():
                raise TimeoutError(
                    "timeout: the test never reported its result (signature "
                    "0x{:08x}, tohost 0x{:08x})".format(
                        self.handshake.signature_addr,
                        self.handshake.tohost_addr))

    # -- error injection -----------------------------------------------------

    def inject_error(self):
        """Injects an error response into the very next dside transaction
        (mirrors the UVM ``inject_error()`` / ``error_synch`` semantics).
        Available to tests that want a transient error; the dedicated error
        test uses the permanent ``error_addrs`` set instead."""
        self._inject_error = True

    def _take_error_for(self, addr: int) -> bool:
        """Consume the pending one-shot error injection for addr. Accesses
        to the handshake addresses never see an injected error."""
        if addr in self._no_error_addrs:
            return False
        if self._inject_error:
            self._inject_error = False
            return True
        return addr in self.cfg.error_addrs

    # -- delay selection -----------------------------------------------------

    def _grant_delay(self):
        if self._zero_delays:
            return 0
        return random.choices(*self._gnt_choices)[0]

    def _valid_delay(self):
        if self._zero_delays:
            return 0
        return random.choices(*self._valid_choices)[0]

    def _spurious_delay(self):
        return random.randint(self.cfg.spurious_response_delay_min,
                              self.cfg.spurious_response_delay_max)

    # -- bus driving ----------------------------------------------------------

    async def _wait_cycles(self, n):
        for _ in range(n):
            await RisingEdge(self.dut.clk_o)

    async def _drive_grant(self, gnt, tag):
        logger.debug("grant cycle %d (%s)",
                     cocotb.utils.get_sim_time(unit="ns") // 10, tag)
        gnt.value = 1
        await RisingEdge(self.dut.clk_o)
        gnt.value = 0

    async def _drive_response(self, rvalid, rdata, err, response_data,
                              error, tag):
        """Drive one rvalid pulse with the given payload. rvalid must stay a
        strict one-cycle pulse: holding it high while the notify handshake
        runs would keep the LSU's load-complete signal asserted and clobber
        the register-file write of the next instruction."""
        logger.debug("rvalid cycle %d (%s) data=0x%08x",
                     cocotb.utils.get_sim_time(unit="ns") // 10, tag,
                     response_data)
        rdata.value = response_data
        err.value = error
        rvalid.value = 1
        await RisingEdge(self.dut.clk_o)
        rvalid.value = 0
        err.value = 0

    # -- spurious responses ----------------------------------------------------

    async def _maybe_spurious(self):
        """Fire a spurious dside response when the inter-spurious delay has
        elapsed and the data port is not serving a request (the UVM
        ``outstanding_accesses == 0`` condition). Not forwarded to
        ``on_access``: the core ignores stray responses, so the co-simulator
        must never see them."""
        if not self.cfg.enable_spurious_response:
            return
        delay = self._spurious_delay()
        while True:
            await RisingEdge(self.dut.clk_o)
            if delay > 0:
                delay -= 1
                continue
            if self._data_bus_busy:
                # A request is being served; never disturb the data port.
                delay = self._spurious_delay()
                continue
            # UVM semantics: a spurious response has a random payload and a
            # random error bit. The core gates stray responses with
            # (outstanding_load_wb | expecting_load_resp_id), so this is
            # safe: with no outstanding access nothing consumes it.
            spurious_data = random.getrandbits(32)
            spurious_err = random.getrandbits(1)
            logger.debug("spurious response cycle %d (data=0x%08x, err=%d)",
                         cocotb.utils.get_sim_time(unit="ns") // 10,
                         spurious_data, spurious_err)
            await self._drive_response(
                self.dut.data_rvalid_i, self.dut.data_rdata_i,
                self.dut.data_err_i, spurious_data, spurious_err,
                "spurious")
            self.spurious_count += 1
            delay = self._spurious_delay()

    # -- request serving --------------------------------------------------------

    async def _serve_instr_port(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            if not int(self.dut.instr_req_o.value):
                continue
            request_addr = int(self.dut.instr_addr_o.value)
            response_data = self.read_word(request_addr)
            logger.debug("instr req cycle %d addr 0x%08x -> 0x%08x",
                         cocotb.utils.get_sim_time(unit="ns") // 10,
                         request_addr, response_data)
            await self._wait_cycles(self._grant_delay())
            await self._drive_grant(self.dut.instr_gnt_i, "instr")
            await self._wait_cycles(self._valid_delay())
            await self._drive_response(
                self.dut.instr_rvalid_i, self.dut.instr_rdata_i,
                self.dut.instr_err_i, response_data, 0, "instr")

    async def _serve_data_port(self):
        while True:
            await RisingEdge(self.dut.clk_o)
            if not int(self.dut.data_req_o.value):
                continue
            # From here until the response pulse completes the data port is
            # ours; the spurious-response task must stay away.
            self._data_bus_busy = True

            # Capture the request phase; the outputs deassert in the grant
            # cycle, so everything must be read here.
            request_addr = int(self.dut.data_addr_o.value)
            is_store = bool(int(self.dut.data_we_o.value))
            request_data = int(self.dut.data_wdata_o.value)
            request_be = int(self.dut.data_be_o.value)

            await self._wait_cycles(self._grant_delay())
            await self._drive_grant(self.dut.data_gnt_i, "data")

            # Access classification latched by the tb at the address phase
            # (request && grant), where the LSU-internal probe values are
            # architecturally meaningful; the bus-level signals alone
            # cannot distinguish the halves of a misaligned access from
            # aligned byte/halfword accesses.
            mis_first = int(self.dut.data_misaligned_first_o.value)
            mis_second = int(self.dut.data_misaligned_second_o.value)
            mis_first_saw_err = int(
                self.dut.data_misaligned_first_saw_error_o.value)
            m_mode = int(self.dut.data_m_mode_o.value)

            error = self._take_error_for(request_addr)
            await self._wait_cycles(self._valid_delay())

            if is_store:
                self.store_count += 1
                # Stores commit at the response phase, not the request
                # phase: a granted request can still be answered with an
                # error, in which case the store must leave no trace in
                # memory.
                if not error:
                    self.write_word(request_addr, request_data, request_be)
                    if self.handshake.is_watched(request_addr):
                        self.observed_writes.append(
                            (request_addr, request_data))
                        self.done_event.set()
                response_data = 0
                access_data = request_data
            else:
                self.load_count += 1
                if error:
                    # An errored load returns garbage; the memory model must
                    # not be touched (UVM: rand_data on error).
                    response_data = random.getrandbits(32)
                else:
                    response_data = self.read_word(request_addr)
                access_data = response_data

            await self._drive_response(
                self.dut.data_rvalid_i, self.dut.data_rdata_i,
                self.dut.data_err_i, response_data, int(error), "data")
            self._data_bus_busy = False

            if error:
                self.error_count += 1

            # For loads the DUT drives the byte enables of the access size
            # (e.g. 2'b11 for a halfword), which the co-simulator checks
            # against its own access.
            access = DsideAccess(store=is_store, addr=request_addr,
                                 data=access_data, be=request_be,
                                 error=error, misaligned_first=mis_first,
                                 misaligned_second=mis_second,
                                 misaligned_first_saw_error=mis_first_saw_err,
                                 m_mode_access=m_mode)

            # The co-simulator contract requires accesses to be notified once
            # their response is seen; the notify handshake runs while the bus
            # is idle.
            if self.on_access is not None:
                await self.on_access(access)

    async def run(self):
        # The loops run forever; gather reports a failure in any of them by
        # cancelling the others and raising.
        await gather(self._serve_instr_port(), self._serve_data_port(),
                     self._maybe_spurious())
