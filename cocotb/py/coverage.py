# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Functional coverage model for the cocotb verification environment (M4).

Rebuilds the microarchitectural coverage points of
``doc/03_reference/coverage_plan.rst`` that are observable from this tb's
signals. The UVM fcov interfaces sample DUT internals at the ID/EX stage
(``dv/uvm/core_ibex/fcov/core_ibex_fcov_if.sv``); this tb only exposes RVFI
retirements, the bus behaviour the memory agent itself drove, and the LSU
misaligned/privilege probes. Every coverpoint below therefore samples at
the retirement point or the bus response, not at the ID/EX stage; the skew
is documented per group and repeated in the report. Plan points that need
signals or configuration this tb does not have are listed in ``SKIPPED``
with the reason instead of being left silently uncovered.

Coverage data flows through two sample points that already exist (nothing
here runs its own clock loop):

- the RVFI monitor calls ``on_retire`` once per retired instruction;
- the memory agent calls ``on_bus_event`` once per served transaction.

``report()`` prints the cocotb-coverage text report to the log and writes
an XML database (``coverage_db.export_to_xml``) so the results survive the
simulation. Between tests the hit counters are reset, because the
coverage_db is a process-wide singleton while every test starts a fresh
run.
"""

import logging
from collections import OrderedDict
from pathlib import Path

from cocotb_coverage.coverage import CoverCross, CoverPoint, coverage_db

logger = logging.getLogger("cocotb.coverage")

# ---------------------------------------------------------------------------
# Instruction classification
# ---------------------------------------------------------------------------
# Translation of the UVM ``id_instr_category`` decoding (core_ibex_fcov_if.sv).
# The input is always the 32-bit uncompressed encoding: RVFI presents the
# expanded instruction, so the compressed forms are invisible here (the
# CompressedIllegal category of the plan cannot be observed).

_OP_LUI, _OP_AUIPC, _OP_JAL, _OP_JALR = 0x37, 0x17, 0x6F, 0x67
_OP_BRANCH, _OP_LOAD, _OP_STORE = 0x63, 0x03, 0x23
_OP_OP_IMM, _OP_OP, _OP_SYSTEM, _OP_MISC_MEM = 0x13, 0x33, 0x73, 0x0F

CATEGORY_NAMES = (
    "ALU", "Mul", "Div", "Branch", "Jump", "Load", "Store", "CSRAccess",
    "EBreakDbg", "EBreakExc", "ECall", "MRet", "DRet", "WFI", "Fence",
    "FenceI", "UncompressedIllegal", "CSRIllegal", "PrivIllegal", "Other",
)


def instr_category(insn: int, debug_mode: bool) -> str:
    """Classify one retired instruction, following the UVM decoding table.
    ``debug_mode`` splits EBREAK the same way the UVM ``debug_ebreakm_i/u``
    check does, except the source is the retired item's RVFI debug-mode bit
    instead of the dcsr/privilege combination (the tb has no dcsr probe).

    RVFI presents compressed instructions as the 16-bit encoding
    zero-extended into 32 bits (and ``rvfi_ext_expanded_insn`` is only
    asserted for instructions the IF stage actually expanded), so the
    compressed encodings are classified directly from their RVC quadrants.
    The UVM fcov samples the ID stage instead, where every instruction is
    already expanded; the quadrant classification lands in the same
    category for every legal encoding, so the two stay comparable.
    """
    if insn & 0x3 != 0x3:
        return compressed_category(insn & 0xFFFF, debug_mode)
    opcode = insn & 0x7F
    funct3 = (insn >> 12) & 0x7

    if opcode in (_OP_LUI, _OP_AUIPC, _OP_OP_IMM):
        return "ALU"
    if opcode in (_OP_JAL, _OP_JALR):
        return "Jump"
    if opcode == _OP_BRANCH:
        return "Branch"
    if opcode == _OP_LOAD:
        return "Load"
    if opcode == _OP_STORE:
        return "Store"
    if opcode == _OP_OP:
        # {insn[26], funct3} == {1, 001} and the RV32I/RV32B funct7 set are
        # ALU operations; funct7 == 1 is the M extension (Div when bit 14).
        if ((insn >> 26) & 1) and funct3 == 1:
            return "ALU"
        funct7 = insn >> 25
        if funct7 in {0x00, 0x20, 0x30, 0x34, 0x14, 0x10, 0x05, 0x04, 0x24}:
            return "ALU"
        if funct7 == 0x01:
            return "Div" if (insn >> 14) & 1 else "Mul"
        return "Other"
    if opcode == _OP_SYSTEM:
        if funct3 != 0:
            return "CSRAccess"
        imm12 = insn >> 20
        if imm12 == 0x000:
            return "ECall"
        if imm12 == 0x001:
            return "EBreakDbg" if debug_mode else "EBreakExc"
        if imm12 == 0x302:
            return "MRet"
        if imm12 == 0x7B2:
            return "DRet"
        if imm12 == 0x105:
            return "WFI"
        return "Other"
    if opcode == _OP_MISC_MEM:
        if funct3 == 0:
            return "Fence"
        if funct3 == 1:
            return "FenceI"
        return "Other"
    return "Other"


def compressed_category(insn16: int, debug_mode: bool) -> str:
    """Category of one 16-bit RVC encoding by quadrant. The category is
    what its 32-bit expansion would decode to (the UVM ID-stage view), so
    the legal encodings land in the same bins as the uncompressed table
    above; unknown or illegal encodings stay in ``Other`` (the plan's
    CompressedIllegal bin is documented as unobservable in SKIPPED)."""
    if insn16 == 0:
        # The all-zero encoding retires only as a trap artifact; it must
        # not fall into the ALU bin of the c.addi4spn quadrant below.
        return "Other"
    op = insn16 & 0x3
    funct3 = (insn16 >> 13) & 0x7

    if op == 0:  # quadrant C0
        if funct3 == 0:
            return "ALU"                      # c.addi4spn
        if funct3 == 2:
            return "Load"                     # c.lw
        if funct3 == 6:
            return "Store"                    # c.sw
        return "Other"
    if op == 1:  # quadrant C1
        if funct3 in (0, 1):
            return "ALU"                      # c.addi / c.addiw
        if funct3 == 2:
            return "ALU"                      # c.li
        if funct3 == 3:
            return "ALU"                      # c.addi16sp / c.lui
        if funct3 == 4:
            return "ALU"                      # c.srli/srai/andi/sub/xor/or/and
        if funct3 == 5:
            return "Jump"                     # c.j
        if funct3 == 6:
            return "Branch"                   # c.beqz
        if funct3 == 7:
            return "Branch"                   # c.bnez
        return "Other"
    if op == 2:  # quadrant C2
        if funct3 == 0:
            return "ALU"                      # c.slli
        if funct3 == 2:
            return "Load"                     # c.lwsp
        if funct3 == 4:
            # c.jr / c.mv (bit 12 clear) or c.ebreak / c.jalr / c.add
            if insn16 & 0x1000:
                rs2 = (insn16 >> 2) & 0x1F
                rs1 = (insn16 >> 7) & 0x1F
                if rs2 != 0:
                    return "ALU"              # c.add
                if rs1 != 0:
                    return "Jump"             # c.jalr
                return ("EBreakDbg" if debug_mode else "EBreakExc")
            if (insn16 >> 7) & 0x1F:
                return "Jump"                 # c.jr
            return "ALU"                      # c.mv (rd and rs2 non-zero)
        if funct3 == 6:
            return "Store"                    # c.swsp
        return "Other"
    return "Other"


def retire_category(insn: int, mode: int, trap: int, debug_mode: bool) -> str:
    """Category of a retired instruction, with the trap refinement applied
    in the same order as the UVM decoding (illegal categories override the
    opcode category). The refinement is approximate: the DUT's illegal /
    fetch-error flags are not visible on RVFI, so a trap plus an unknown or
    privileged encoding is taken as the illegal category."""
    category = instr_category(insn, debug_mode)
    if not trap:
        return category
    if category == "Other":
        return "UncompressedIllegal"
    if category == "CSRAccess":
        return "PrivIllegal" if mode == 0 else "CSRIllegal"
    if category in ("MRet", "WFI") and mode == 0:
        return "PrivIllegal"
    return category


# ---------------------------------------------------------------------------
# CSR access decoding
# ---------------------------------------------------------------------------
# The plan samples CSR reads/writes at the ibex_cs_registers interface; the
# RVFI-side approximation decodes the CSR instruction itself: CSRRW reads
# unless rd is x0, the CSRRS/CSRRC forms always read, and a write happens
# whenever rs1 (or the immediate) is non-zero. Addresses are grouped into
# buckets instead of the plan's per-CSR bins because the bin set must be
# fixed at elaboration time.

CSR_BUCKETS = (
    "mstatus", "misa", "mie/mip", "mtvec", "mepc", "mcause", "mtval",
    "mscratch", "mcounteren/mcountinhibit", "mhpmevent", "pmpcfg", "pmpaddr",
    "tselect/tdata", "debug", "mcycle/minstret", "satp",
    "medeleg/mideleg", "fcsr/vcsr", "mhartid",
    "mvendorid/marchid/mimpid", "other",
)


def csr_bucket(addr: int) -> str:
    if addr == 0x300:
        return "mstatus"
    if addr == 0x301:
        return "misa"
    if addr in (0x304, 0x344):
        return "mie/mip"
    if addr == 0x305:
        return "mtvec"
    if addr == 0x341:
        return "mepc"
    if addr == 0x342:
        return "mcause"
    if addr == 0x343:
        return "mtval"
    if addr == 0x340:
        return "mscratch"
    if addr in (0x306, 0x320):
        return "mcounteren/mcountinhibit"
    if 0x323 <= addr <= 0x33F:
        return "mhpmevent"
    if 0x3A0 <= addr <= 0x3AF:
        return "pmpcfg"
    if 0x3B0 <= addr <= 0x3BF:
        return "pmpaddr"
    if 0x7A0 <= addr <= 0x7A3:
        return "tselect/tdata"
    if 0x7B0 <= addr <= 0x7B3:
        return "debug"
    if addr in (0xB00, 0xB02, 0xB80, 0xB82):
        return "mcycle/minstret"
    if addr == 0x180:
        return "satp"
    if addr in (0x302, 0x303):
        return "medeleg/mideleg"
    if addr in (0x003, 0x007):
        return "fcsr/vcsr"
    if addr == 0xF14:
        return "mhartid"
    if addr in (0xF11, 0xF12, 0xF13):
        return "mvendorid/marchid/mimpid"
    return "other"


def csr_reads(insn: int) -> bool:
    """Whether the CSR instruction performs a read."""
    funct3 = (insn >> 12) & 0x7
    if funct3 in (1, 5):
        return ((insn >> 7) & 0x1F) != 0
    if funct3 in (2, 3, 6, 7):
        return True
    return False


def csr_writes(insn: int) -> bool:
    """Whether the CSR instruction performs a write."""
    funct3 = (insn >> 12) & 0x7
    if funct3 in (1, 2, 3):
        return ((insn >> 15) & 0x1F) != 0
    if funct3 in (5, 6, 7):
        return (insn >> 15) != 0
    return False


# ---------------------------------------------------------------------------
# Interrupt decoding
# ---------------------------------------------------------------------------
# The plan's cp_interrupt_taken samples the ID-stage IRQ lines; the RVFI-side
# approximation derives the taken line from the pre/post mip difference on
# the retired trap item (the bit that cleared is the line that was taken).

INTERRUPT_LINES = ("software", "timer", "external", "fast", "nmi", "none")

# RVFI mode values to their coverage names; the single representation of
# the privilege level in this model (the ID-side and LSU-side
# coverpoints both use these names).
_MODE_NAMES = {0: "user", 3: "machine"}


def priv_mode_name(mode: int) -> str:
    return _MODE_NAMES.get(mode, "other")


def interrupt_line(pre_mip: int, post_mip: int, nmi: int, nmi_int: int):
    """Name of the interrupt line taken by this item, or None."""
    if nmi or nmi_int:
        return "nmi"
    taken = pre_mip & ~post_mip
    if taken & (1 << 3):
        return "software"
    if taken & (1 << 7):
        return "timer"
    if taken & (1 << 11):
        return "external"
    for bit in range(16, 31):
        if taken & (1 << bit):
            return "fast"
    return None


# ---------------------------------------------------------------------------
# Points of the plan this tb cannot observe
# ---------------------------------------------------------------------------
SKIPPED = {
    # Sampled from ID/EX stage internals that this tb does not probe.
    "cp_stall_type_id (and stall_cross)":
        "stall categories come from ID-stage stall signals; the tb only "
        "sees retirement timing",
    "cp_wb_reg_no_load_hz / cp_mem_raw_hz":
        "hazard detection is internal to the ID stage",
    "cp_if/id/wb_stage_state, pipe_cross":
        "per-stage fill/stall state is not visible from RVFI or the bus",
    "cp_controller_fsm, cp_controller_fsm_sleep, irq/debug_wfi_cross":
        "the controller FSM is an internal signal",
    "cp_single_step_instr/taken/exception, cp_insn_trigger_enter_debug":
        "debug single-step and trigger matching have no RVFI representation",
    "cp_double_fault":
        "the plan point needs the controller's double-fault detection; the "
        "tb only exposes the top-level double_fault_seen_o pulse",
    "cp_interrupt_taken (ID-stage form), interrupt_taken_instr_cross":
        "the ID-stage IRQ lines are not probed; the RVFI approximation "
        "(cp_interrupt_taken) samples taken interrupts on trap items only, "
        "and no test takes a maskable interrupt on this WritebackStage=0 tb "
        "(PLAN.md pitfall #14)",
    "instr_unstalled, *_instr_cross on stalls":
        "stall/unstall cycles are ID-stage facts",
    # Structurally impossible in this tb, not just unsampled.
    "cp_id_instr_category FetchError bin":
        "an instruction-fetch error trap retires with an insn of zero on "
        "RVFI, indistinguishable from an illegal-instruction trap, so the "
        "fetch-error category cannot be derived (the iside_error_test "
        "still verifies the trap itself)",
    "cp_id_instr_category CompressedIllegal bin":
        "RVFI presents the expanded instruction, so the illegal compressed "
        "encoding is not distinguishable from an illegal uncompressed one",
    "imem_req_gnt_rvalid / dmem_req_gnt_valid":
        "the agent serves transactions strictly serially, so a new request "
        "can never be granted in the same cycle a response lands",
    # Not applicable to this tb's DUT configuration (SecureIbex=0,
    # ICache=0, MemECC=0, WritebackStage=0, no lockstep/DIT/dummy).
    "cp_data_ind_timing*, cp_dummy_instr*, dummy_instr_config_cross":
        "DIT and dummy-instruction insertion are off in this configuration",
    "cp_rf_a/b_ecc_err, rf_ecc_err_cross, cp_icache_ecc_err, "
    "cp_mem_load/store_ecc_err, cp_lockstep_err, cp_rf_glitch_err":
        "ECC, lockstep and glitch detection are off in this configuration",
    "cp_icache_enable, FenceI icache interaction":
        "the DUT is built without an icache",
    "PMP coverpoints and crosses":
        "the tb has no PMP configuration probes and no test configures "
        "the PMP regions",
    "cp_csr_invalid_read_only / cp_csr_invalid_write":
        "the plan's invalid-CSR accesses need the DUT's illegal-CSR "
        "classification, which RVFI does not expose",
    "cp_warl_check_*":
        "WARL field behaviour is not observable from RVFI",
    "cp_fetch_enable":
        "fetch_enable_i is tied on in this tb",
    "cp_data_ind_timing / security countermeasure mapping":
        "see the security countermeasure rows above",
}


# ---------------------------------------------------------------------------
# Coverage model
# ---------------------------------------------------------------------------

class IbexCoverage:
    """The cocotb functional coverage model.

    Coverpoints and crosses are declared as method decorators (the
    cocotb-coverage convention: the decorator wraps the sampling method,
    and the wrapped call both transforms the argument and matches the
    bins). Every sampling method takes the fields it covers; the wrapper's
    ``xf`` extracts the value to match, because the library's ``vname``
    path is unusable on bound methods (it indexes the post-self argument
    tuple with the pre-self signature index). Crosses must be sampled
    after their coverpoints: a CoverCross consumes the ``_new_hits`` each
    coverpoint recorded on its most recent sample.
    """

    # Instructions and privilege, sampled on every retirement.
    @CoverPoint("cov.cp_id_instr_category", xf=lambda category: category,
                bins=list(CATEGORY_NAMES),
                bins_labels=list(CATEGORY_NAMES))
    def _sample_instr_category(self, category):
        pass

    @CoverPoint("cov.cp_priv_mode_id", xf=lambda mode: mode,
                bins=["user", "machine"],
                bins_labels=["user", "machine"])
    def _sample_priv_mode_id(self, mode):
        pass

    @CoverPoint("cov.cp_priv_mode_lsu", xf=lambda mode: mode,
                bins=["user", "machine"],
                bins_labels=["user", "machine"])
    def _sample_priv_mode_lsu(self, mode):
        pass

    # Exceptions/interrupts/debug, sampled on the retirement that carries
    # them.
    @CoverPoint("cov.cp_interrupt_taken", xf=lambda line: line,
                bins=list(INTERRUPT_LINES),
                bins_labels=list(INTERRUPT_LINES))
    def _sample_interrupt_taken(self, line):
        pass

    @CoverPoint("cov.cp_nmi_taken", xf=lambda nmi: nmi,
                bins=[0, 1], bins_labels=["no", "yes"])
    def _sample_nmi_taken(self, nmi):
        pass

    @CoverPoint("cov.cp_debug_req", xf=lambda debug_req: debug_req,
                bins=[0, 1], bins_labels=["no", "yes"])
    def _sample_debug_req(self, debug_req):
        pass

    @CoverPoint("cov.cp_debug_mode", xf=lambda debug_mode: debug_mode,
                bins=[0, 1], bins_labels=["no", "yes"])
    def _sample_debug_mode(self, debug_mode):
        pass

    @CoverPoint("cov.cp_trap_category", xf=lambda category: category,
                bins=list(CATEGORY_NAMES),
                bins_labels=list(CATEGORY_NAMES))
    def _sample_trap_category(self, category):
        pass

    # CSR reads/writes, sampled on retired CSR instructions.
    @CoverPoint("cov.cp_csr_read_only", xf=lambda bucket: bucket,
                bins=list(CSR_BUCKETS), bins_labels=list(CSR_BUCKETS))
    def _sample_csr_read_only(self, bucket):
        pass

    @CoverPoint("cov.cp_csr_write", xf=lambda bucket: bucket,
                bins=list(CSR_BUCKETS), bins_labels=list(CSR_BUCKETS))
    def _sample_csr_write(self, bucket):
        pass

    # Memory interface behaviour, sampled by the agent on every transaction
    # it has served (the agent serialises transactions, so its added delay
    # of zero is the protocol minimum: the UVM "single cycle" response).
    @CoverPoint("cov.cp_imem_response_latency",
                xf=lambda latency: latency,
                bins=["single_cycle", "multi_cycle"],
                bins_labels=["single_cycle", "multi_cycle"])
    def _sample_imem_latency(self, latency):
        pass

    @CoverPoint("cov.cp_dmem_response_latency",
                xf=lambda latency: latency,
                bins=["single_cycle", "multi_cycle"],
                bins_labels=["single_cycle", "multi_cycle"])
    def _sample_dmem_latency(self, latency):
        pass

    @CoverPoint("cov.cp_misaligned_first_data_err", xf=lambda err: err,
                bins=["no_error", "error"],
                bins_labels=["no_error", "error"])
    def _sample_misaligned_first_err(self, err):
        pass

    @CoverPoint("cov.cp_misaligned_second_data_err", xf=lambda err: err,
                bins=["no_error", "error"],
                bins_labels=["no_error", "error"])
    def _sample_misaligned_second_err(self, err):
        pass

    # Crosses. Each is sampled right after its coverpoints so the
    # CoverCross sees their latest hits.
    @CoverCross("cov.priv_mode_instr_cross",
                items=["cov.cp_priv_mode_id", "cov.cp_id_instr_category"])
    def _sample_priv_mode_instr_cross(self, mode, category):
        pass

    @CoverCross("cov.interrupt_taken_instr_cross",
                items=["cov.cp_interrupt_taken", "cov.cp_id_instr_category"])
    def _sample_interrupt_taken_instr_cross(self, line, category):
        pass

    @CoverCross("cov.nmi_taken_instr_cross",
                items=["cov.cp_nmi_taken", "cov.cp_id_instr_category"])
    def _sample_nmi_taken_instr_cross(self, nmi, category):
        pass

    @CoverCross("cov.csr_read_only_priv_cross",
                items=["cov.cp_csr_read_only", "cov.cp_priv_mode_id"])
    def _sample_csr_read_only_priv_cross(self, bucket, mode):
        pass

    @CoverCross("cov.csr_write_priv_cross",
                items=["cov.cp_csr_write", "cov.cp_priv_mode_id"])
    def _sample_csr_write_priv_cross(self, bucket, mode):
        pass

    # -- entry points used by the monitor and the agent ----------------------

    def on_retire(self, item):
        """Called by the RVFI monitor for every retired instruction. Sample
        order matters: coverpoints first, then their crosses."""
        # Classify the expanded encoding when the monitor captured one;
        # rvfi_insn alone presents compressed instructions as the 16-bit
        # encoding zero-extended, which the classifier cannot decode.
        insn = item.expanded_insn if item.expanded_valid else item.insn
        mode = priv_mode_name(item.mode)
        category = retire_category(insn, item.mode, item.trap,
                                   item.debug_mode)
        self._sample_instr_category(category)
        self._sample_priv_mode_id(mode)
        self._sample_priv_mode_instr_cross(mode, category)

        self._sample_debug_req(item.debug_req)
        self._sample_debug_mode(item.debug_mode)
        self._sample_nmi_taken(1 if (item.nmi or item.nmi_int) else 0)
        self._sample_nmi_taken_instr_cross(
            1 if (item.nmi or item.nmi_int) else 0, category)

        if item.trap:
            self._sample_trap_category(category)
            if item.intr:
                line = interrupt_line(item.pre_mip, item.post_mip, item.nmi,
                                      item.nmi_int) or "none"
                self._sample_interrupt_taken(line)
                self._sample_interrupt_taken_instr_cross(line, category)

        # CSR instructions: a read and a write are sampled independently, as
        # the plan's cp_csr_read_only / cp_csr_write do.
        if category == "CSRAccess":
            addr = insn >> 20
            if csr_reads(insn):
                bucket = csr_bucket(addr)
                self._sample_csr_read_only(bucket)
                self._sample_csr_read_only_priv_cross(bucket, mode)
            if csr_writes(insn):
                bucket = csr_bucket(addr)
                self._sample_csr_write(bucket)
                self._sample_csr_write_priv_cross(bucket, mode)

    def on_bus_event(self, evt):
        """Called by the memory agent for every served transaction. The
        agent serialises transactions, so zero added delay is the protocol
        minimum and maps to the plan's ``single_cycle`` bin."""
        latency = "single_cycle" if evt.added_delay == 0 else "multi_cycle"
        if evt.is_instr:
            self._sample_imem_latency(latency)
        else:
            self._sample_dmem_latency(latency)
            self._sample_priv_mode_lsu(
                "machine" if evt.m_mode else "user")
            if evt.misaligned_first:
                self._sample_misaligned_first_err(
                    "error" if (evt.misaligned_first_saw_error
                                or evt.error) else "no_error")
            if evt.misaligned_second:
                self._sample_misaligned_second_err(
                    "error" if evt.error else "no_error")

    # -- reporting -----------------------------------------------------------

    def report(self, xml_path: Path):
        """Log the text report, export the XML database and reset the hit
        counters for the next test (the coverage_db is process-wide)."""
        logger.info("coverage report (sampling at retirement/bus response; "
                    "see the per-group notes and the skipped list):")
        coverage_db.report_coverage(logger.info, bins=True, node="cov")
        coverage_db.export_to_xml(str(xml_path))
        logger.info("coverage database written to %s", xml_path)
        for name, reason in SKIPPED.items():
            logger.info("coverage skipped %s: %s", name, reason)
        self._reset_hits()

    def _reset_hits(self):
        """Zero the hit counters between tests.

        This must reset the cocotb-coverage internals in place rather
        than deleting and re-registering the coverpoints: the library
        (2.0) has no reset API, CoverPoint.__new__ caches objects by
        name, and _update_size only ever grows the parents, so
        re-registration would leave the decorator closures bound to dead
        objects with permanently inflated sizes. The private fields are
        asserted first so a library upgrade fails loudly instead of
        accumulating coverage under different internals.
        """
        for name in list(coverage_db):
            obj = coverage_db[name]
            if type(obj) in (CoverPoint, CoverCross):
                assert hasattr(obj, "_hits"), (
                    "cocotb-coverage internals changed ({}: no _hits); "
                    "the reset relies on them, pin the library "
                    "version".format(name))
                obj._hits = OrderedDict.fromkeys(obj._hits.keys(), 0)
            obj._coverage = 0
            obj._new_hits = []
