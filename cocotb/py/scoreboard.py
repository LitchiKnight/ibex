# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Scoreboard for the cocotb verification environment.

Mirrors ``dv/uvm/core_ibex/common/ibex_cosim_agent/ibex_cosim_scoreboard.sv``:
each retired instruction captured by the RVFI monitor is stepped through the
SpikeCosim co-simulator (the unchanged C++ comparison core from dv/cosim,
see cocotb/PLAN.md) and the results are compared. The steps go through the
``Cosim`` semantic layer (py/cosim.py); the raw command handshake lives in
py/cosim_channel.py. A mismatch raises a structured
``CosimMismatchError`` carrying the instruction identity, the decoded trap
crash dump and the error strings drained from the co-simulator.

``drain()`` is the tests' catch-up interface: it returns once every item
retired up to the call has been processed, so the tests never poll the
processed/retired counters themselves.
"""

import logging

from cocotb.triggers import Event, First, Timer, select

from cosim import Cosim, CosimError, CosimMismatchError
from rvfi_monitor import decode_crash_dump

logger = logging.getLogger("cocotb.scoreboard")

# Upper bound on how many error strings the mismatch report pulls from the
# co-simulator; anything beyond it is printed by the drain in finish().
MAX_MISMATCH_ERRORS = 16

# How long finish() waits for the comparison loop to exit after stop().
# One step handshake is a handful of cycles and a wedge is detected after
# 1000, so 100 us (10k cycles) is generous; exceeding it means a real bug.
STOP_GRACE_NS = 100_000


class Scoreboard:
    """Consume RVFI items in retirement order and step each one through the
    co-simulator.

    ``error`` holds the exception that stopped the scoreboard (a structured
    ``CosimMismatchError`` or a transport failure); ``processed`` counts
    consumed items. ``drain()`` waits until a snapshot of the monitor's
    retired count has been processed.
    """

    def __init__(self, cosim: Cosim, monitor, iside_error_source):
        self.cosim = cosim
        self.monitor = monitor
        # The memory agent, which remembers the address of any instruction
        # fetch it answered with an error; the scoreboard forwards it to
        # the co-simulator before the trap's step. Required: omitting it
        # would silently drop the fetch-error comparison, so no default is
        # offered.
        self._iside_error_source = iside_error_source
        self.processed = 0
        self.error = None
        self._progress = Event()
        # finish() sets these to end the comparison loop: after the
        # co-simulator is released the program's end loop keeps retiring,
        # and there is nothing left to compare against.
        self._stopped = False
        self._stop_event = Event()
        # Set when the comparison loop has actually exited (both the normal
        # and the error path); finish() waits on it before releasing so no
        # step command can be in flight across the release.
        self._exited = Event()

    async def run(self):
        try:
            while True:
                # select() races the stop event against the next RVFI
                # item and cancels the loser; index 0 means stop() won.
                # _stopped is checked again even when the item won: stop()
                # and the release may both run while this coroutine is
                # waiting to resume, and a post-release step is an error.
                index, item = await select(self._stop_event.wait(),
                                           self.monitor.get())
                if index == 0 or self._stopped:
                    return
                await self.step_item(item)
                self.processed += 1
                self._progress.set()
        except Exception as error:
            self.error = error
            logger.error("scoreboard stopped: %s", error)
        finally:
            self._exited.set()

    def stop(self):
        """End the comparison loop; finish() calls this once the
        co-simulator is being released. Idempotent."""
        self._stopped = True
        self._stop_event.set()

    async def step_item(self, item):
        if item.irq_only:
            # IRQ-only RVFI events notify the co-simulator about interrupts
            # that fire without a retired instruction (e.g. while the
            # pipeline is empty). Mirrors the UVM scoreboard's irq_only
            # branch: set_nmi, set_nmi_int, set_mip(pre_mip, pre_mip) - no
            # debug_req, no mcycle, no step.
            await self.cosim.set_mip(item.nmi, item.nmi_int, item.pre_mip)
            return

        # An instruction-fetch error answered by the memory agent must
        # reach the co-simulator before the trap's step (the UVM
        # scoreboard's riscv_cosim_set_iside_error from its ifetch queue).
        # Every retired item consumes the pending slot, mirroring the UVM
        # queue's per-item order match: only a trap item retiring at the
        # faulting address forwards the error, anything else means the
        # faulted fetch was flushed or never reached RVFI and the stale
        # expectation is dropped (injecting it would fail a later step
        # with a misattributed mismatch).
        iside_addr = self._iside_error_source.consume_iside_error()
        if iside_addr is not None:
            if item.trap and (item.pc & ~0x3) == iside_addr:
                await self.cosim.set_iside_error(iside_addr)
            else:
                logger.error("dropped iside error at 0x%08x: retiring item "
                             "pc=0x%08x trap=%d does not match it",
                             iside_addr, item.pc, item.trap)

        ok = await self.cosim.step(
            pc=item.pc,
            rd_wdata=item.rd_wdata,
            rd_addr=item.rd_addr,
            rf_wr_suppress=item.rf_wr_suppress,
            trap=item.trap,
            nmi_int=item.nmi_int,
            nmi=item.nmi,
            debug_req=item.debug_req,
            pre_mip=item.pre_mip,
            post_mip=item.post_mip,
            mcycle=item.mcycle,
        )
        if not ok:
            errors = await self._collect_errors()
            crash_dump = (
                decode_crash_dump(item.crash_dump) if item.trap else None
            )
            raise CosimMismatchError(order=item.order, pc=item.pc,
                                     insn=item.insn, trap=item.trap,
                                     errors=errors, crash_dump=crash_dump)

    async def _collect_errors(self):
        """Fetch the comparison error strings for the mismatch report, then
        drain (print and clear) them so the sim log keeps the full list."""
        errors = []
        for index in range(MAX_MISMATCH_ERRORS):
            message = await self.cosim.error_str(index)
            if not message:
                break
            errors.append(message)
        drained = await self.cosim.drain_errors()
        if drained > len(errors):
            errors.append("{} more error(s), see the sim log".format(
                drained - len(errors)))
        return errors

    async def drain(self, timeout_ns: int) -> None:
        """Wait until every item retired up to this call has been processed.

        The tests call this after the program reports its result; the
        trailing instructions of the program's infinite end loop keep
        retiring, so this drains to a snapshot of
        ``monitor.retired_count`` taken here rather than to queue
        emptiness. Raises the stored error (a structured mismatch or a
        transport failure) when the scoreboard died, and TimeoutError when
        it does not catch up in time.
        """
        target = self.monitor.retired_count
        while True:
            if self.processed >= target:
                return
            if self.error is not None:
                raise self.error
            self._progress.clear()
            # Re-check after the clear: an increment between the check above
            # and the clear would otherwise be lost and the wait could block
            # forever.
            if self.processed >= target:
                return
            if self.error is not None:
                raise self.error
            await First(self._progress.wait(), Timer(timeout_ns, unit="ns"))
            if not self._progress.is_set():
                raise TimeoutError(
                    "scoreboard did not catch up: processed {}, retired {} "
                    "(no progress for {} ns)".format(
                        self.processed, target, timeout_ns))

    async def finish(self) -> int:
        # A comparison error appearing between the test's drain() and the
        # release below would otherwise be written to self.error and never
        # read again, silently passing the test; raise it instead.
        if self.error is not None:
            raise self.error
        insn_cnt = await self.cosim.get_insn_cnt()
        drained = await self.cosim.drain_errors()
        if drained:
            logger.warning("%d undrained cosim error(s) at release", drained)
        if self.error is not None:
            raise self.error
        # The comparison ends here: signal the loop, wait until it has
        # actually exited so no step command is in flight across the
        # release, then release.
        self.stop()
        await First(self._exited.wait(), Timer(STOP_GRACE_NS, unit="ns"))
        if not self._exited.is_set():
            raise CosimError("scoreboard did not stop before release")
        await self.cosim.release()
        logger.info("SpikeCosim released after %d matched instructions",
                    insn_cnt)
        return insn_cnt
