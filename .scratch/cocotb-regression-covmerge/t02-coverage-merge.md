---
title: 覆盖率 merge 工具与文本总表报告
labels: [wayfinder:task]
parent: map.md
blocked_by: []
assignee: claude
state: closed
---

## Question

实现覆盖率 merge 工具:合并 `out/coverage_*.xml` 为 `out/coverage_merged.xml`,并输出文本总表报告(每 coverpoint/交叉命中率 + 总体百分比)。

> **并行执行的所有权调整(图定案)**:本 ticket 与 t01 由两个子代理并行执行,Makefile 由 t01 独占(它加 `regression` 目标)。本 ticket **不得修改 `cocotb/Makefile`**;交付 `cocotb/merge_cov.py` 独立 CLI 即可,`make merge_cov` 目标与回归收尾自动调用由 t03 接线。仿真运行须用 `SIM_BUILD=t02_sim_build`(cocotb 的 Makefile.sim 支持)避免与 t01 并发冲突。

**事实(已查证)**:
- cocotb-coverage 已精确锁版 2.0(`cocotb/requirements.txt`),自带 `merge_coverage(logger, merged_file_name, *files)`,支持合并 XML/YAML 覆盖率文件
- 每个测试已产出 `out/coverage_<suite>_<test>.xml`(bin 级 hits 计数,M4 产物)
- coverage_db 为进程级单例,跨进程只能走文件合并

**需敲定并落地**:
1. **合并机制(核心决策)**:先做小实验验证库 `merge_coverage` 的合并语义(hits 是否求和、百分比是否按合并后重算、coverpoint/交叉是否对齐);语义正确则复用,不符则自研 XML 聚合(解析→按 abs_name 对齐→hits 求和→重算百分比,参考库源码实现)
2. **文本总表报告**:格式(按 XML 顺序的控制台表格:每 coverpoint 命中 bins/size 与百分比 + 总体百分比);输出位置(推荐控制台打印 + 落 `out/merge_report.txt`)
3. **边界行为**:无 XML 目录、单 XML、多 XML 三种场景合理处理;覆盖率被 `+ibex_cocotb_cov=0` 关闭的 run 无 XML,merge 不报错

**已定约束(图定案)**:文本报告(无 HTML);merge 工具对既有 per-test XML 独立可用(不依赖回归编排器);回归收尾自动调用由 t03 负责接线。

## Verification

- 手工合并两个已知 XML 的 hits 与工具输出逐 bin 比对一致,百分比重算正确
- `make merge_cov` 在无 XML、单 XML、多 XML 三种场景行为合理且不破坏验证结论

## Resolution

**合并机制:复用库 `merge_coverage`**。实验证实其语义正确(用真实 gen_1/gen_2 XML 验证):
- bin hits 按 `abs_name` 求和,390 个 bin 逐项与手工求和比对零失配
- bin 跨过 `at_least` 阈值时按 weight 计入父级 coverage,百分比自底向上重算(如 cp_id_instr_category 9/20→10/20、总体 13.59%→14.36%)
- 仅单侧存在的元素带其 coverage 并入;交叉(cross)按同规则合并

**交付**:`cocotb/merge_cov.py` 独立 CLI(`--xml-dir`/`--merged`/`--report`,默认 `out/coverage_merged.xml` + `out/merge_report.txt`,控制台同步打印总表:每 coverpoint/交叉 covered/size 与 pct + 总体)。关键设计:`coverage_merged.xml` 自身匹配 glob 但**永不作输入**(否则二次合并双计数);不可解析的文件(并行回归仍在写入)警告跳过;覆盖率是副产物,所有预期边界场景 exit 0,仅意外内部错误非零。

**边界行为(实测)**:无 XML 目录 → 提示 + exit 0;单 XML → 正确合并;双 XML → 正确合并。`make merge_cov` 目标与回归收尾接线按所有权调整留给 t03。

**验证证据**:三场景运行通过;合并 XML 与手工求和逐 bin 比对 390/390 一致;交叉 hits 求和核对通过。
