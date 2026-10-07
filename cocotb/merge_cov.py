# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Coverage merge tool for the cocotb verification environment (t02).

Merges the per-test coverage databases written by py/coverage.py
(``out/coverage_<suite>_<test>.xml``) into one database and prints a text
summary table (per coverpoint/cross covered bins and percentage, plus the
overall total). Usage::

    python3 merge_cov.py [--xml-dir DIR] [--merged FILE] [--report FILE]

The merge reuses ``cocotb_coverage.coverage.merge_coverage`` (the library is
pinned in cocotb/requirements.txt). An experiment confirmed its semantics on
the real databases: bin hits are summed per ``abs_name``, a bin crossing the
``at_least`` threshold adds its weight to the parent coverage, percentages
are recomputed bottom-up, and elements present in only one file are added
with their coverage propagated; crosses merge by the same bin rule.

The tool never fails a run over coverage (coverage is a byproduct, not the
verification verdict): missing files (a run with +ibex_cocotb_cov=0 produces
none) and files it cannot parse (e.g. a parallel regression is still writing
them) are skipped with a warning, and the exit code stays 0 for every
expected boundary case. Only an unexpected internal error exits non-zero.
"""

import argparse
import logging
import sys
import textwrap
import xml.etree.ElementTree as et
from pathlib import Path

logger = logging.getLogger("cocotb.merge_cov")

MERGED_NAME = "coverage_merged.xml"


def _parseable(path: Path) -> bool:
    try:
        et.parse(str(path))
        return True
    except et.ParseError:
        return False


def _merge(files: list[Path], merged_path: Path) -> None:
    from cocotb_coverage.coverage import merge_coverage

    merge_coverage(logger.info, str(merged_path), *[str(f) for f in files])


def _summary_table(merged_path: Path, files: list[Path]) -> str:
    """Render the text report of the merged database."""
    root = et.parse(str(merged_path)).getroot()
    cov = root.find("cov")
    lines = ["merged coverage report: {} file(s)".format(len(files))]
    lines += textwrap.wrap(", ".join(f.name for f in files), width=100,
                           initial_indent="  ", subsequent_indent="  ")
    lines += [
        "overall: {}/{} ({:.2f}%)".format(
            root.get("coverage"), root.get("size"),
            float(root.get("cover_percentage"))),
        "",
        "{:<40} {:>10} {:>7}".format("point", "covered", "pct"),
        "-" * 60,
    ]
    for child in cov:
        lines.append("{:<40} {:>5}/{} {:>6.1f}".format(
            child.get("abs_name", "").removeprefix("top.cov."),
            child.get("coverage"), child.get("size"),
            float(child.get("cover_percentage"))))
    return "\n".join(lines)


def main(argv=None) -> int:
    default_dir = Path(__file__).resolve().parent / "out"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--xml-dir", type=Path, default=default_dir,
                        help="directory holding coverage_*.xml "
                             "(default: cocotb/out)")
    parser.add_argument("--merged", type=Path,
                        default=default_dir / MERGED_NAME,
                        help="merged database path (default: "
                             "out/coverage_merged.xml)")
    parser.add_argument("--report", type=Path,
                        default=default_dir / "merge_report.txt",
                        help="text report path (default: "
                             "out/merge_report.txt)")
    parser.add_argument("files", nargs="*", type=Path,
                        help="explicit per-test XMLs to merge; when given, "
                             "the directory glob is not used (a regression "
                             "passes exactly the XMLs of the tests it ran, "
                             "so stale files from earlier runs stay out)")
    args = parser.parse_args(argv)

    xml_dir = args.xml_dir
    merged_path = args.merged
    report_path = args.report

    if args.files:
        files = sorted(args.files)
    else:
        files = sorted(xml_dir.glob("coverage_*.xml")) if xml_dir.is_dir() else []
    # The merged database itself matches the glob; it is an output, never an
    # input, otherwise the second merge would double-count every hit.
    files = [f for f in files if f.name != MERGED_NAME]

    valid = [f for f in files if _parseable(f)]
    for f in files:
        if f not in valid:
            logger.warning("skipping unparseable coverage file %s "
                           "(still being written?)", f)

    if not valid:
        text = ("no per-test coverage files to merge in {}\n"
                "(a run with +ibex_cocotb_cov=0 produces none; "
                "this is not an error)".format(xml_dir))
    else:
        _merge(valid, merged_path)
        text = _summary_table(merged_path, valid)

    print(text)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + "\n")
    logger.info("merge report written to %s", report_path)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
