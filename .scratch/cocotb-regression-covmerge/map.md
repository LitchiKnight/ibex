---
title: cocotb 回归与覆盖率 merge
labels: [wayfinder:map]
state: closed
---

## Destination

cocotb 验证环境获得回归与覆盖率 merge 能力:`make regression` 按显式测试清单默认全量并行运行、跑完汇总结果(失败保留现场、非零退出码),结束时自动合并各测试覆盖率并输出文本总表;验收 = 全量 PASS + 负向验证 + 零改动约束。

## Notes

- **目的地形态**:地图直达实现(每个 ticket 既决策又实现,地图走完时功能已落地并验收)
- **硬约束**:不修改任何上游/现有仓库文件(rtl/、dv/、vendor/、doc/ 等零改动);`cocotb/` 目录内文件(含其 Makefile)属于本环境可正常演进;比对核心必须复用 `dv/cosim` 的 SpikeCosim C++ 库,不得重写
- **开工必读**:`cocotb/PLAN.md` §9(跨会话状态 + 坑 #1–#16),特别是:rvalid 严格单脉冲(坑 #3)、tohost==1 判读冲突(坑 #13)、WB=0 中断取指限制(坑 #14)、coverage_db 为进程级单例(M4)
- **项目惯例**:每轮实现合入前过 ousterhout-software-design 审查(sub-agent);回复与文档用中文
- **收尾**:遵循 AGENTS.md 的 AOCI 维护流程(稳定后 `aoci_maintain` 一次)

## Decisions so far

<!-- 每关闭一个 ticket 追加一行:标题(链接) — 答案要点 -->

- [t02 覆盖率 merge 工具与文本总表](t02-coverage-merge.md) — 复用库 `merge_coverage`(实测语义正确:hits 按 abs_name 求和、百分比重算);交付 `cocotb/merge_cov.py` 独立 CLI(merged XML + 文本总表);`make merge_cov` 目标与回归收尾接线归 t03
- [t01 回归编排器与测试清单](t01-regression-runner.md) — elaboration 一次 + 复用编译 sim 二进制并行跑(实测 -j4 墙钟 10.9s vs 串行 29.1s,约 2.7×);`cocotb/run.py` + `cocotb/testlist.yaml`(34 条 curated)+ Makefile `regression` 目标(解析期 ifeq 规避 cocotb 自带 regression 名冲突);全量 34/34 PASS
- [t03 整合、负向验证与验收](t03-integration-acceptance.md) — run.py 汇总后自动 merge + Makefile `merge_cov` 目标;负向验证当场抓获假 PASS 真 bug(失败判定改读 results.xml,坑 #17);最终验收:全量 34/34 PASS、合并覆盖率 109/390=27.95%、上游零修改。目的地达成,地图关闭

## Not yet specified

- (无 — 全量回归实测约 8–11s,时长基准问题随 t01/t03 实测自行消解,无需专门 ticket)

## Out of scope

- debug agent + debug 测试、MHPM 计数通道、WB=1 变体(NMI/中断取指验证)、riscv-arch-tests 语料接入 — PLAN.md §9 的可选后续,留待下一个 effort
- HTML 覆盖率报告(本次只做 merged XML + 文本报告,图定案)
- 对上游文件(rtl/、dv/、vendor/、doc/ 等)的任何修改 — 零改动约束
- testlist.yaml 格式错误时的友好报错(当前 yaml traceback 退出,行为正确仅不友好)与 fence_i 等未 curated rv32ui 用例的建链支持(需 zifencei) — t03 遗留观察,超出本地图目的地,留待新 effort
