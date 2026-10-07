---
title: 整合、负向验证与验收
labels: [wayfinder:task]
parent: map.md
blocked_by: [t01-regression-runner.md, t02-coverage-merge.md]
assignee: claude
state: closed
---

## Question

把覆盖率 merge 接进回归收尾,完成负向验证与文档收口,达成地图目的地:

1. **自动 merge 接线**:回归全部跑完后自动执行合并(覆盖率开启时),汇总表附带合并覆盖率总表;`make merge_cov` 保持可独立手动触发
2. **负向验证**:注入一个必败测试(推荐临时在清单中加一条指向不存在程序的条目,或临时改 gen seed 使程序必然失配),确认汇总表正确标 FAIL、退出码非零、日志保留现场;**验证后必须移除注入,不得留在清单**
3. **文档收口**:`cocotb/PLAN.md` §9 追加本轮状态(回归 + merge 里程碑、新坑位如有);`PARITY.md` 仅当对拍事实变化时更新
4. **最终验收**:全量回归 PASS;`out/coverage_merged.xml` + 文本总表生成;`git status` 仅新增文件、零修改(上游文件与 cocotb 既有文件均不得被意外改动)

**已定约束(图定案)**:验收 = 全量 PASS + 负向验证 + 零改动;失败保留现场、非零退出码。

## Verification

- 负向验证与最终验收全部满足(见上第 2、4 条)
- PLAN.md §9 状态更新如实记录

## Resolution

**自动 merge 接线**:`run.py` 汇总表之后自动调用 `merge_cov.py`(subprocess,输出并入回归结果;merge 边界场景恒 exit 0,不检查退出码);Makefile 新增独立 `merge_cov` phony 目标(`python3 merge_cov.py`)。

**负向验证(当场抓获一个真 bug)**:临时注入必败测试(注释 error_test 分支的毒地址 plusarg,陷阱不触发)→ 首轮发现 run.py **假 PASS**:编译后的 sim 二进制在 cocotb 测试失败时仍退出 0,初版只信进程退出码。修复:新增 `results_failures()` 读 `COCOTB_RESULTS_FILE`(JUnit 格式 failures+errors==0 才算 PASS),退出码仅作次信号,results.xml 缺失/不可解析按 FAIL。修复后重跑:汇总正确标 FAIL、退出码 1、`out/logs/sw_error_test.log` 保留现场。注入(Makefile/testlist.yaml)已逐字节还原(diff 校验 RESTORED-BYTE-IDENTICAL)。

**文档收口**:`cocotb/PLAN.md` §9 追加"回归与覆盖率 merge 已完成"里程碑(交付物、验证结果、新坑 #17 假 PASS、#18 regression 目标名冲突),状态行同步;PARITY.md 对拍事实未变,未改动。

**最终验收(实测)**:
- `make regression` 全量 34/34 PASS,退出码 0,墙钟约 8s;汇总表后附合并覆盖率总表(34 个 XML → 总体 109/390 = 27.95%)
- `make merge_cov` 独立目标 exit 0;`out/coverage_merged.xml`(40KB)+ `out/merge_report.txt` 生成
- `git status`:cocotb/ 内仅 Makefile 修改 + run.py/testlist.yaml/merge_cov.py 新增;上游零修改(除主会话的 AOCI 资产)

**遗留观察**(未处理,不入本轮范围):testlist.yaml 格式错误时 run.py 以 yaml 异常 traceback 退出(非零退出码,行为正确但不友好);fence_i 等未 curated 的 rv32ui 用例因汇编缺 zifencei 在建链期失败。
