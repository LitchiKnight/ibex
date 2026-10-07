---
title: 回归编排器与测试清单
labels: [wayfinder:task]
parent: map.md
blocked_by: []
assignee: claude
state: closed
---

## Question

为 cocotb 环境实现回归编排器与显式测试清单,使 `make regression` 默认全量并行运行、跑完汇总结果。需敲定并落地:

1. **编排器形态与入口**(推荐:`cocotb/run.py` 单文件编排器 + `cocotb/Makefile` 新增 `regression` 目标;PLAN.md §4 曾规划 `run.py 回归编排`,沿用该命名)
2. **测试清单格式与内容**(推荐:`cocotb/testlist.yaml`,显式 curated;内容以 PARITY.md 的 29/29 为准:rv32ui 7 条 + rv32mi 14 条 + sw 3 条(irq_test/error_test/iside_error_test)+ gen 默认 10 seed;新用例必须加入清单才进回归)
3. **并行执行路径(核心决策)**:所有测试共用同一个 DUT(仅测试程序与 TESTCASE 不同),推荐 elaboration 一次、N 个测试复用已编译 sim 二进制、以不同 `COCOTB_TESTCASE`/plusarg 并行运行;若该路径不可靠,回退逐测试 `make sim`(此时必须解决多进程共享 `sim_build/` 的并发冲突,如 per-test SIM_BUILD 目录)
4. **结果收集**:每测试日志落 `out/logs/<suite>_<test>.log`;汇总表(逐测试 PASS/FAIL + 失败原因);全部跑完、失败不中断;有失败时退出码非零;per-test 超时保护

**已定约束(图定案,不可推翻)**:显式测试清单 + 默认全量;失败跑完汇总、保留现场;gen 多 seed 与并行 -j 为 PLAN.md §7.4 既定;零改动(上游文件)。

**注意**:负向验证(人为注入失败)不在本 ticket,由 t03 负责。

## Verification

- `make regression` 默认全量运行,结果与 PARITY.md 29/29 语料一致(全部 PASS、退出码 0)
- 汇总表逐测试正确;`out/logs/` 每测试一个日志
- 并行确实生效(全量墙钟时间显著低于串行逐测试)
- 单测路径 `make sim TESTSUITE=... TEST=...` 行为不变(回归是新增能力,不破坏现有入口)

## Resolution

**交付**:`cocotb/run.py`(编排器,新增)+ `cocotb/testlist.yaml`(显式 curated 清单:rv32ui 7 + rv32mi 14 + sw 3 + gen seeds 1-10 = 34 条,与 PARITY.md 29/29 语料一致)+ `cocotb/Makefile` 新增 `regression` 与 `regress-vars` 目标(本 ticket 独占 Makefile)。

**并行执行路径(核心决策)**:实验证实"elaboration 一次、复用已编译 sim 二进制"可行——所有测试共用同一 DUT,直接以不同环境变量 + plusarg 运行 `sim_build/Vtop`(每测试 ~0.2s,启动开销主导)。所需运行时环境:`GPI_USERS`/`PYGPI_PYTHON_BIN`/`COCOTB_RESULTS_FILE`(每测试独立,否则并行写冲突)/`COCOTB_TEST_MODULES`/`COCOTB_TESTCASE`/`COCOTB_TOPLEVEL`/`TOPLEVEL_LANG`/`COCOTB_TRUST_INERTIAL_WRITES`;这些值经 `regress-vars` 目标由 Makefile 单一权威提供,run.py 不自行推导 TESTCASE/plusarg/工具路径。

**结果收集**:每测试日志 `out/logs/<suite>_<test>.log` + 独立 `results.xml`;汇总表(逐测试状态+耗时,失败附首个 ERROR/FAIL 行与日志路径);全部跑完、失败不中断、退出码非零;`--timeout` 默认 1200s;`--tests` 子串过滤(供 t03 负向验证);`-j` 默认 4。

**新发现的坑(重要)**:cocotb 2.1 自带 makefile 用 `sim → regression → results.xml` 跑单仿真,`regression` 是它占用的目标名;同名 full-run recipe 会污染每次 `make sim`。解法:`regression` 的 recipe 用解析期 `ifeq ($(MAKECMDGOALS),regression)` 条件化——作为 `sim` 的前置依赖时该目标无 recipe,`make sim` 保持单测语义。副作用(已记录在 Makefile 注释):results.xml 过期时 `make regression` 会先跑一次默认单仿真(该名合并了 results.xml 前置依赖,无法移除)。

**验证证据(实测)**:
- `make regression` 全量 34/34 PASS,退出码 0,墙钟 10.9s(-j4,并行)
- `python3 run.py -j 1` 全量 34/34 PASS,墙钟 29.1s → 并行约 2.7× 加速
- `make sim TESTSUITE=sw TEST=error_test` 单测路径不变:TESTS=1 PASS=1;`make -n sim` 确认无 run.py 参与
- `out/logs/` 34 个日志,逐测试汇总表正确
