#!/usr/bin/env python3
# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Regression orchestrator for the Ibex cocotb verification environment.

Reads the explicit curated testlist (testlist.yaml), builds every test
binary and resolves every test's runtime configuration through the
Makefile (regress-vars, the single source), elaborates the tb once, then
runs the same compiled sim binary for every test in parallel (the DUT is
identical across tests; only the loaded program and the cocotb testcase
differ). Every run gets its own random COCOTB_RANDOM_SEED, recorded as the
first line of its log. One log per test lands in <out>/logs/, a summary
table is printed, and the exit code is non-zero when any test fails (all
tests still run to completion).

Usage:
  python3 run.py                # full list, 4 parallel simulations
  python3 run.py -j 8           # 8 parallel simulations
  python3 run.py --tests gen    # only entries whose id contains 'gen'
  python3 run.py --timeout 600  # per-test wall-clock cap in seconds
"""

import argparse
import concurrent.futures
import dataclasses
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as et

import yaml

# The cov_paths module lives in py/, which has no __init__.py: the in-sim
# convention imports it flat (see py/test_lib.py). Importing it as
# py.cov_paths would resolve the installed third-party `py` package from
# site-packages instead of this directory, so py/ is put on sys.path and
# the flat name is used, matching the simulator side.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "py"))
from cov_paths import coverage_xml_path  # noqa: E402

COCOTB_DIR = os.path.dirname(os.path.abspath(__file__))
TESTLIST = os.path.join(COCOTB_DIR, "testlist.yaml")


def make(*args):
    """Run make in the cocotb directory, capturing its output."""
    return subprocess.run(["make", *args], cwd=COCOTB_DIR,
                          capture_output=True, text=True)


@dataclasses.dataclass
class RunResult:
    """One test's regression outcome."""
    suite: str
    test: str
    status: str
    elapsed: float
    log: str
    seed: int


def test_config(suite, test):
    """Build the test binary and resolve its runtime configuration through
    the Makefile (single source).

    The TESTSUITE branches in the Makefile own the testcase name, the bin
    path and the extra plusargs (e.g. the poisoned address of the error
    tests); run.py must never re-derive them by itself. regress-vars
    prints one KEY='value' line per variable, parsed with shlex so a value
    may contain '=' or spaces. Building and querying share one make
    invocation.
    """
    r = make("-s", "build_test", "regress-vars",
             f"TESTSUITE={suite}", f"TEST={test}")
    if r.returncode != 0:
        raise RuntimeError(f"build_test/regress-vars failed for "
                           f"{suite}/{test}: {r.stderr[-400:]}")
    cfg = {}
    for line in r.stdout.splitlines():
        tokens = shlex.split(line.strip())
        if len(tokens) == 1 and "=" in tokens[0]:
            key, value = tokens[0].split("=", 1)
            cfg[key] = value
    return cfg


def load_testlist():
    """Expand testlist.yaml into (suite, test) pairs."""
    with open(TESTLIST) as f:
        data = yaml.safe_load(f)
    tests = []
    for suite, entries in data.items():
        if suite == "gen":
            for seed in range(1, int(entries["seeds"]) + 1):
                tests.append(("gen", str(seed)))
        else:
            for name in entries:
                tests.append((suite, name))
    return tests


def failure_reason(log_path):
    """First ERROR/FAIL line of a failing run's log, for the summary."""
    try:
        with open(log_path, errors="replace") as f:
            for line in f:
                if re.search(r"\b(ERROR|FAIL)\b", line):
                    return line.strip()[:120]
    except OSError:
        pass
    return "see log"


def results_failures(results_path):
    """Count failing/erroring testcases in a cocotb results.xml file.

    The compiled sim binary exits 0 even when a cocotb test fails (the
    failure is only recorded in the JUnit-style results file), so the
    verdict must come from here, not from the process exit code.
    """
    try:
        root = et.parse(results_path).getroot()
    except (OSError, et.ParseError):
        return None  # missing or unparseable: never report PASS
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return None
    return int(suite.get("failures", 0)) + int(suite.get("errors", 0))


def run_one(suite, test, cfg, timeout, log_dir, sim_bin):
    """Run one test against the shared compiled sim binary."""
    name = f"{suite}_{test}"
    log_path = os.path.join(log_dir, name + ".log")

    # Fresh per-run random seed for the simulation. Without
    # COCOTB_RANDOM_SEED cocotb falls back to int(time.time()), so tests
    # starting in the same second (parallel runs) would share one random
    # sequence.
    seed = secrets.randbelow(2**31)

    # Runtime environment the cocotb makefiles export for the compiled sim
    # binary; every value comes from the Makefile (regress-vars) so the
    # tool paths, testcase names and plusargs are never duplicated here.
    env = os.environ.copy()
    env.update({
        "GPI_USERS": cfg["GPI_USERS"],
        "PYGPI_PYTHON_BIN": cfg["PYGPI_PYTHON_BIN"],
        "COCOTB_TEST_MODULES": cfg["MODULE"],
        "COCOTB_TESTCASE": cfg["TESTCASE"],
        "COCOTB_TOPLEVEL": cfg["TOPLEVEL"],
        "TOPLEVEL_LANG": cfg["TOPLEVEL_LANG"],
        # Per-test results file: parallel runs must not share results.xml.
        "COCOTB_RESULTS_FILE": os.path.join(log_dir, name + ".results.xml"),
        "COCOTB_TRUST_INERTIAL_WRITES": "1",
        "COCOTB_RANDOM_SEED": str(seed),
    })
    cmd = [sim_bin,
           f"+ibex_cocotb_bin={cfg['BIN']}",
           f"+ibex_cocotb_load_addr={cfg['LOAD_ADDR']}"]
    if cfg.get("EXTRA_PLUSARGS"):
        cmd += shlex.split(cfg["EXTRA_PLUSARGS"])

    start = time.monotonic()
    with open(log_path, "w") as log:
        # The seed is the first line of the log: a failing run can be
        # reproduced with COCOTB_RANDOM_SEED=<seed> make sim ...
        # Flush before the sim starts: the subprocess writes to the raw
        # fd, so a buffered line would land at the end of the file instead.
        log.write(f"COCOTB_RANDOM_SEED={seed}\n")
        log.flush()
        try:
            r = subprocess.run(cmd, cwd=COCOTB_DIR, env=env,
                               stdout=log, stderr=subprocess.STDOUT,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            return RunResult(suite, test, "TIMEOUT",
                             time.monotonic() - start, log_path, seed)
    elapsed = time.monotonic() - start
    # PASS needs a clean sim exit AND zero recorded test failures; the sim
    # binary exits 0 on a failing testcase, so the results file is decisive.
    bad = results_failures(env["COCOTB_RESULTS_FILE"])
    status = "PASS" if (r.returncode == 0 and bad == 0) else "FAIL"
    return RunResult(suite, test, status, elapsed, log_path, seed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-j", "--jobs", type=int, default=4,
                        help="parallel simulations (default 4)")
    parser.add_argument("--timeout", type=int, default=1200,
                        help="per-test wall-clock cap in seconds (default 1200)")
    parser.add_argument("--tests", default=None,
                        help="only run entries whose id contains this substring")
    args = parser.parse_args()

    tests = load_testlist()
    if args.tests:
        tests = [(s, t) for s, t in tests if args.tests in f"{s}_{t}"]
    if not tests:
        print("no tests selected", file=sys.stderr)
        return 1

    # Phase 1: build every test binary and resolve its configuration
    # through the Makefile (parallel; each test has distinct outputs).
    def resolve(st):
        try:
            return st, test_config(*st), ""
        except RuntimeError as exc:
            return st, None, str(exc)

    build_start = time.monotonic()
    cfgs = {}
    build_failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for st, cfg, err in ex.map(resolve, tests):
            if cfg is None:
                build_failures.append((st, err))
            else:
                cfgs[st] = cfg
    if build_failures:
        for (suite, test), err in build_failures:
            print(f"  BUILD FAIL {suite}/{test}: {err}", file=sys.stderr)
        print("build phase failed; no simulation started", file=sys.stderr)
        return 1
    print(f"build phase: {time.monotonic() - build_start:.1f}s")

    # Shared paths (identical for every test; the Makefile resolves them).
    first = cfgs[tests[0]]
    out_dir = first["OUT_DIR"]
    sim_bin = os.path.join(first["SIM_BUILD"], "Vtop")
    log_dir = os.path.join(out_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Phase 2: elaborate the tb once; every test reuses this binary.
    r = make(f"{first['SIM_BUILD']}/Vtop")
    if r.returncode != 0:
        print("elaboration failed:\n" + r.stderr[-1000:], file=sys.stderr)
        return 1

    # Phase 3: run all tests, then report; failures never stop the run.
    run_start = time.monotonic()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = [ex.submit(run_one, s, t, cfgs[(s, t)], args.timeout,
                             log_dir, sim_bin) for s, t in tests]
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            results.append(res)
            print(f"  {res.suite}/{res.test:<18} "
                  f"{res.status:<7} {res.elapsed:6.1f}s")
    results.sort(key=lambda r: (r.suite, r.test))

    # Summary table; keep the failure reason on its own line so the table
    # stays aligned.
    passed = sum(1 for r in results if r.status == "PASS")
    print("=== summary ===")
    for r in results:
        if r.status == "PASS":
            continue
        print(f"  {r.suite}/{r.test} {r.status}: "
              f"{failure_reason(r.log)}")
        print(f"    log: {os.path.relpath(r.log, COCOTB_DIR)}, "
              f"seed: {r.seed}")
    print(f"PASS {passed}/{len(results)}, "
          f"elapsed {time.monotonic() - run_start:.1f}s")

    # Coverage merge (t03): coverage is a byproduct, not the verdict, so
    # the merge runs after every regression and its exit code is never
    # checked (merge_cov.py exits 0 for every expected boundary case, e.g.
    # no per-test XMLs when coverage was disabled). Only the XMLs of the
    # tests this run actually executed are merged, so stale coverage_*.xml
    # files left in out/ by earlier runs stay out of the report. The path
    # naming rule comes from py/cov_paths.py (the same authority as the
    # writer), and the merge runs under the simulator's pinned Python so
    # it sees the same cocotb-coverage version.
    cov_xmls = [str(coverage_xml_path(out_dir, r.suite, r.test))
                for r in results
                if os.path.exists(coverage_xml_path(out_dir, r.suite,
                                                    r.test))]
    python = first["PYGPI_PYTHON_BIN"] or sys.executable
    m = subprocess.run([python, "merge_cov.py", *cov_xmls,
                        "--merged", os.path.join(out_dir,
                                                "coverage_merged.xml"),
                        "--report", os.path.join(out_dir,
                                                 "merge_report.txt")],
                       cwd=COCOTB_DIR, capture_output=True, text=True)
    print(m.stdout.rstrip())
    if m.returncode != 0:
        print("coverage merge failed:\n" + m.stderr[-400:], file=sys.stderr)

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
