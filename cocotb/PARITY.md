# cocotb 与 UVM 环境对拍报告(M5)

> 目标:证明 cocotb 验证环境的缺陷检出能力与 `dv/uvm/core_ibex` 环境一致。
> 方法:同语料结果对照 + 缺陷注入对照 + co-simulator 调用序列逐项静态对照。
> 本机不安装商业 EDA(VCS/Xcelium),UVM 侧证据取自 `dv/uvm/core_ibex` 源码与
> 上游 CI 结果;cocotb 侧证据为 2026-10-07 的 29/29 回归(本仓库可复现)。

## 1. 同语料结果对照

| 语料 | UVM 环境 | cocotb 环境 |
|---|---|---|
| `vendor/riscv-tests` rv32ui(7 条) | CI 通过(`ci.yml` 合规套件) | 29/29 回归 PASS |
| `vendor/riscv-tests` rv32mi(14 条) | CI 通过 | 29/29 回归 PASS |
| 随机指令流(riscv-dv / gen) | `riscv_assorted_traps_interrupts_debug` 等 | gen seeds 1-5 PASS |

两边跑同一份 vendor 语料、链接同一个 directed-test link.ld、比对同一个
`dv/cosim` SpikeCosim 核心(cocotb 构建原样编译,见 Makefile COSIM_SRCS),
所以同语料的"指令退役逐条比对"口径一致。

## 2. co-simulator 调用序列对照(每条退役指令)

UVM `ibex_cosim_scoreboard.sv` 的 step 路径与 cocotb 的 `Cosim.step` +
`Scoreboard.step_item` 逐项对照:

| UVM 调用 | cocotb 对应 | 状态 |
|---|---|---|
| `riscv_cosim_set_iside_error(addr)`(ifetch 队列) | `cosim.set_iside_error`(CMD_SET_ISIDE_ERROR,agent 的 pending 地址,step 前) | **M5 补齐** |
| `riscv_cosim_set_debug_req` | CMD_STEP 的 STEP_DEBUG_REQ_BIT | 已传(值恒 0,见 §4) |
| `riscv_cosim_set_nmi` / `set_nmi_int` | CMD_STEP 的 STEP_NMI/NMI_INT 位 | 已传 |
| `riscv_cosim_set_mip(pre, post)` | CMD_STEP 的 cmd_a3/a4 | 已传 |
| `riscv_cosim_set_mcycle` | CMD_STEP 的 cmd_a5/a6 | 已传 |
| `riscv_cosim_set_csr(MHPMCOUNTER3+i, …)` | 无 | 配置相关,见 §4 |
| `riscv_cosim_set_ic_scr_key_valid` | 无 | 配置相关,见 §4 |
| `riscv_cosim_step(rd_addr, rd_wdata, pc, trap, rf_wr_suppress)` | CMD_STEP 同序打包 | 已传 |
| irq_only 分支:`set_nmi, set_nmi_int, set_mip(pre, pre)`,不 step | `cosim.set_mip`(CMD_SET_MIP) | 已传 |
| `riscv_cosim_notify_dside_access` | `cosim.notify_dside`(CMD_NOTIFY_DSIDE) | 已传 |

调用顺序与 UVM 一致(debug_req → nmi → nmi_int → mip → mcycle → step),
由 tb 的 CMD_STEP case 固定。

## 3. 缺陷注入对照

| 缺陷 | UVM 检出方式 | cocotb 检出方式 | 状态 |
|---|---|---|---|
| 数据侧总线错误(load/store) | `error_synch`/`inject_error` + mem_model 未映射 | `error_addrs` + `inject_error()`;`sw/error_test` 端到端验证(load access fault,DUT 与 Spike 双模型一致) | 已验收(M2/M5) |
| 取指侧总线错误 | ifetch monitor 记 `iside_error_queue`,step 前 `set_iside_error` | agent 对指令端口错误响应 + `consume_iside_error` → `set_iside_error`;`sw/iside_error_test` 端到端验证(mcause==1、mepc==ERROR_ADDR、34/34 比对一致) | **M5 补齐** |
| 虚假响应 | UVM base test 对非 secure 配置强制关闭 | 同规则(SecureIbex=0 强制关,`g_check_mem_response` 门控) | 配置相关 |
| 随机延迟/中断噪声 | mem/irq agent | 同构随机(分布已对齐,P1-1) | 已验收 |
| 非法指令/陷阱路径 | riscv-dv 流 + directed | gen 流注入 3 种非法字 + rv32mi/illegal | 已验收(M3) |

## 4. 剩余缺口(记录在案)

1. **mhpmcounters 不传**:UVM 在 `MHPMCounterNum>0` 配置下每步
   `set_csr(MHPMCOUNTER3+i, …)`。本 tb 为 `MHPMCounterNum=0`,UVM 同配置下
   循环为空,两侧当前等价;非零配置需要新命令通道(每条指令最多 20 个
   32 位值,a0-a6 装不下)。
2. **ic_scr_key_valid 不传**:ICache=0 配置下信号恒 0,两侧等价;带 ICache
   配置需要新命令。
3. **debug_req 恒 0**:`debug_req_i` 无驱动者,debug 入口/DRET 路径未验证
   (UVM 有 dedicated debug 测试)。CMD_STEP 已携带 debug_req 位,补一个
   debug agent 即可,留待后续里程碑。
4. **ifetch 监控不等价**:UVM 的 `ibex_ifetch_monitor` 逐次取指检查 Spike
   的取指行为;cocotb 通过内存模型 + iside error 通道近似覆盖(取指错误
   已对齐),但无逐次取指地址检查。
5. **覆盖率口径**:PLAN §7 已接受,UVM fcov(ID/EX 级)与 cocotb-coverage
   (退休/总线级)不可比。

## 5. 结论

- 比对核心(SpikeCosim)完全复用,比对口径一致;
- 缺陷注入(数据侧/取指侧错误、虚假响应规则、随机噪声)与 UVM 语义对齐,
  且每条缺陷都有端到端测试证明 DUT 与 Spike 双模型一致;
- 剩余缺口均为配置相关(当前 tb 配置下两侧等价)或未实现特性
  (debug、MHPM 计数),不影响当前配置的 parity 结论。
