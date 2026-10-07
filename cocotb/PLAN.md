# Ibex cocotb 验证方案

> 目标:在不修改任何现有文件的前提下,新增一套基于 cocotb 的验证环境,替代依赖商业 EDA 工具(VCS / Xcelium)的仿真环节,使功能仿真验证可以在开源工具链(Verilator + Spike + Python)上完整运行。
>
> 状态:方案已定稿,实施待启动。

## 1. 背景

现有验证体系分三层:

| 层 | 位置 | 工具依赖 |
|---|---|---|
| RTL | `rtl/ibex_*.sv` | 无(任何 SV 仿真器) |
| 黄金模型比对库 | `dv/cosim/*.cc/.h` + `dv/uvm/core_ibex/common/ibex_cosim_agent/spike_cosim_dpi.cc` | 仅 Spike(纯 C++) |
| UVM 测试平台 | `dv/uvm/core_ibex` | **商业 EDA**(VCS / Xcelium) |

因此,**依赖商业 EDA 的部分只有 UVM 测试平台本身、riscv-dv 的 UVM 指令生成器、以及 SV 功能覆盖率模型(covergroup)**。其余全链路(比对库、Python 脚本生态、测试语料、RTL、文档规格)均与商业工具无关。

## 2. 设计原则

1. **零改动**:不修改任何现有文件;所有新增内容放入新的 `cocotb/` 目录。
2. **最大化复用**:比对核心(`SpikeCosim` C++ 库)、RVFI 信号路径、Python 脚本、测试语料、文档规格全部原样复用。
3. **新增最小化**:唯一新增的 SystemVerilog 是一个薄测试台顶层(`tb/ibex_cocotb_tb.sv`,例化 `ibex_top_tracing` 并引出信号);其余全部用 Python(cocotb)实现。

## 3. 架构

```
                        Verilator 仿真
┌─────────────────────────────────────────────────────┐
│  cocotb/  (Python 测试平台)                          │
│   mem_agent.py   irq_agent.py   rvfi_monitor.py      │
│         │             │              │               │
│         ▼             ▼              ▼               │
│  tb/ibex_cocotb_tb.sv(新增,例化 ibex_top_tracing)    │
│   [内存接口]  [中断]  [RVFI]  →  DPI                 │
│                                     │               │
└─────────────────────────────────────┼───────────────┘
                                      ▼
        dv/cosim/*.cc + spike_cosim_dpi.cc(原文件原样编译)
                                      ▼
                            SpikeCosim(原 C++ 类)
                                      ▼
                         Spike(黄金模型,ibex_cosim 分支)
```

比对链路说明:cocotb 支持直接调用 RTL 中的 DPI 函数。`scoreboard.py` 通过 `riscv_cosim_step / set_mip / notify_dside_access` 等 DPI 函数驱动**原封不动编译进来的 `SpikeCosim`**,实现逐指令退役比对。

## 4. 新增目录结构

```
cocotb/
├── PLAN.md                       # 本方案文档
├── tb/
│   └── ibex_cocotb_tb.sv          # 唯一新增 SV:例化 ibex_top_tracing,引出 RVFI/内存/中断信号
├── py/
│   ├── env.py                     # 配置对象(语义对齐 core_ibex_env_cfg 的配置项)
│   ├── mem_agent.py               # 内存代理:随机 grant/rvalid 延迟、错误注入、虚假响应
│   ├── irq_agent.py               # 中断代理:软件/定时/外部/fast/NMI 随机中断
│   ├── rvfi_monitor.py            # RVFI 监视器:采集退役指令,打包为 DPI 调用
│   ├── scoreboard.py              # 记分板:步进 SpikeCosim、收集比对错误
│   └── coverage.py                # 覆盖率模型(cocotb-coverage,按 coverage_plan.rst 重建)
├── gen/
│   └── instr_gen.py               # Python 随机指令生成器(借鉴 riscv-dv 的指令配比思路)
├── run.py                         # 回归编排(复用 util/ibex_config.py 与 testlist.yaml)
└── Makefile                       # 编译与运行入口
```

## 5. 组件映射

| 原 UVM 组件 | cocotb 实现 | 复用 / 新建 |
|---|---|---|
| `ibex_cosim_scoreboard` | `py/scoreboard.py` | 新建 Python 逻辑,**底层调用原 C++ `SpikeCosim`** |
| `ibex_rvfi_monitor` | `py/rvfi_monitor.py` | 新建(按 RVFI 接口文档翻译) |
| `ibex_mem_intf_response_agent` | `py/mem_agent.py` | 新建(按 `doc/03_reference/load_store_unit.rst` 协议实现) |
| `irq_agent` | `py/irq_agent.py` | 新建 |
| OpenTitan `mem_model` | Python 内存模型 | 新建(原组件为 SV,Verilator 不可用) |
| `core_ibex_env_cfg` | `py/env.py` | 新建(配置语义对齐) |
| fcov covergroup | `py/coverage.py` | 新建(cocotb-coverage) |
| `testlist.yaml` / `directed_testlist.yaml` | 原文件 | **复用** |
| riscv-dv UVM 指令生成器 | `gen/instr_gen.py` | 新建;**语料复用 `vendor/riscv-tests`、`vendor/riscv-arch-tests`** |
| 程序编译流程 | 现有 `examples/sw/.../common.mk` 的 GCC 部分 | **复用**(纯 GCC,无商业依赖) |
| 回归编排/报告 | `dv/uvm/core_ibex/scripts/` 各 Python 脚本 | **复用**思路,`run.py` 重新编排 |

## 6. 实施里程碑

| 步骤 | 交付物 | 验收标准 |
|---|---|---|
| M1 比对链路打通 | `cocotb/tb/` + 最小 scoreboard | 编译运行一条 `vendor/riscv-tests` rv32ui 用例,`riscv_cosim_step` 比对通过 |
| M2 内存/中断代理 | `py/mem_agent.py`、`py/irq_agent.py` | 随机延迟/错误/中断注入下,compliance 测试集通过 |
| M3 随机指令生成 | `gen/instr_gen.py` + handshake 机制 | 随机程序连续运行无假阳性,能触发非法指令/陷阱路径 |
| M4 覆盖率模型 | `py/coverage.py` | 按 `coverage_plan.rst` 清单逐点实现,报告可生成 |
| M5 对拍验证 | 对拍报告 | 同语料在 UVM 环境(CI)与 cocotb 环境结果一致;缺陷注入两边均能检出 |

## 7. 风险与已接受的代价

1. **覆盖率口径不可比**:cocotb-coverage 数据与原 V2S 报告(Xcelium covergroup)口径不同,只能内部自洽。
2. **随机生成质量落差**:自研生成器初期弱于 riscv-dv 的约束质量;策略为前期以 vendor 语料为主,随机器逐步成熟。
3. **协议断言缺口**:SVA/prim_assert 在 Verilator 不可用,协议违规由 mem_agent 自检逻辑弥补。
4. **性能**:Verilator 单线程慢于 VCS,以并行回归(`-j`)补偿。

## 8. 验收总标准

- 不改动任何现有文件(`git status` 仅新增 `cocotb/`)。
- 一条完整链路(程序生成 → GCC 编译 → Verilator 仿真 → Spike 逐指令比对 → 报告)在无商业 EDA 环境下运行。
- 对拍验证证明 cocotb 环境的缺陷检出能力与原 UVM 环境一致。

## 9. 执行状态(跨会话交接)

- 方案定稿日期:2026-10-06。
- 当前进度:**M1 已完成**(2026-10-07),并已按 Ousterhout《A Philosophy of Software Design》审查完成一轮设计重构(2026-10-07):跨语言位域布局收口为 Python 侧单一权威(SV 侧命名常量解码)、co-sim 参数由 DUT 参数派生、命令接口 null 守卫聚合、未知 opcode/镜像加载失败显式报错、镜像基址由 Makefile plusarg 单一来源、死接口(cmd_ret1/cmd_a7/_cmd_idle)删除、monitor 改用 cocotb.queue.Queue、内存模型记录/判定分离、环境装配抽为 tb_env.bring_up。重构后 7 条用例重跑全部 PASS。
- **M2 已完成**(2026-10-07,验收:23/23 用例 PASS):mem_agent 随机化(随机 grant/rvalid 延迟,分布对齐 UVM `ibex_mem_intf_response_agent_cfg`;虚假响应,含 UVM 同款 SecureIbex 门控规则;错误注入:一次性 `inject_error()` + 固定中毒地址 `error_addrs`,握手地址永不注入;写提交已迁移到响应相)+ 新增 `py/irq_agent.py`(软件/定时/外部/fast/NMI 随机中断,UVM `irq_seq_item` 对齐;NMI 短保持)+ scoreboard 接通 irq-only RVFI 事件(新 CMD_SET_MIP:set_nmi/set_nmi_int/set_mip(pre,pre),不 step;monitor 按事件去重)+ 测试重构(统一 `tests/test_core.py` + `py/test_lib.py` 共享流程;TESTSUITE=rv32ui/rv32mi/sw;专用 `tests/sw/irq_test.S`(maskable+NMI 双 handler,零竞态寄存器约定:s 寄存器存活变量、t0-t2 归 NMI handler、t3-t6 归 maskable handler)+ `tests/sw/error_test.S`(期待 load access fault))。M2 期间发现并修复的坑:misaligned 标志必须由 tb 层级探针(LSU 内部信号,同 UVM `core_ibex_tb_top.sv`)在 (req&&gnt) 地址相位锁存——总线级信号无法区分 misaligned 半次访问与对齐字节/半字访问;scall 类 ecall 测试的通过约定是 tohost 写 TESTNUM==1(失败为 TESTNUM|1337);legacy CSR 别名(sptbr/mbadaddr/sbadaddr)新工具链不再识别,用 `tests/sw/legacy_csr_compat.h` 兼容;tb 的 DbgTriggerEn 已改 1(否则 breakpoint.S 在 tselect 上触发非法 CSR 陷阱且 Spike fork 不建模此行为);GAS 的 `.balign` 在 `.org 0x80` 后行为异常(落在 0x180),专用测试改用显式 `.org` 布局;专用测试需自锚 `.tohost` 段,否则 .data/.bss 落入 0x80001000 被当成 tohost 握手;tb 新增 trap_crash_dump 锁存输出与 LSU 层级探针端口。
- M1 交付物:
  - `tb/ibex_cocotb_tb.sv`:例化 `ibex_top`(RVFI 开启、PMP 开启 4 区域,对齐 UVM 的 riscv-tests 配置),暴露内存/中断/RVFI 端口与 SpikeCosim 命令接口(SV 侧调用 `dv/cosim` 的 DPI 函数,因为 Verilator 的 VPI 不向 cocotb 暴露 DPI 对象)。时钟由 tb 内部 SV 生成(`clk_o` 输出供 Python 作时基)。
  - `dpi/ibex_cocotb_dpi.cc`:薄辅助层(init 时把 `+ibex_cocotb_bin` 指定的镜像 backdoor 写入 cosim、错误上报);比对核心仍为原封不动的 `dv/cosim` 代码。
  - `py/`:env.py(配置对齐 core_ibex_env_cfg)、rvfi_monitor.py、mem_agent.py(最小内存模型)、scoreboard.py(逐指令步进 SpikeCosim、dside 访问通知、命令接口 FIFO 互斥)。
  - `Makefile` + `ibex_rtl.f`(RTL/prims 文件清单,源自 ibex_dv.f 摘取)。
- 本机环境(已装好):Verilator 5.052(brew)、xpack riscv-none-elf-gcc 15.2.0(`~/tools/xpack/`)、Spike(ibex_cosim 分支,commit 见 doc/03_reference/cosim.rst,安装于 `~/tools/spike`,构建时需 `CXXFLAGS=-include ~/tools/spike_compat.h` 修复 macOS 上的 `uint` 类型;安装脚本漏装的头/库已手动补齐:libriscv.a、libfdt.a、riscv/fesvr/fdt/softfloat 头文件、config.h)。srecord 与 fusesoc 未使用(本流程直接调用 verilator/gcc,无需去掉 `.core` 的 `-lutil -lelf`)。
- 实施中的关键坑(后续里程碑务必注意):
  1. **test_en_i 必须接 1**:ibex_top 的时钟门控锁存不响应 Verilator 的 VPI 驱动时钟,test_en_i=0 时核心时钟永不启动(表现为复位无效)。
  2. **时钟用 tb 内部 SV 生成**(`--timing` 下 `#5ns` 翻转),cocotb 的 VPI 时钟与门控锁存/时序调度配合不可靠。
  3. **内存响应 rvalid 必须是严格单周期脉冲**:若在 await notify 期间保持 rvalid,ibex LSU 的 load-complete 会持续多拍并覆盖下一条指令的寄存器写回。
  4. **加载访问的 BE 必须取 DUT 的 data_be_o**(不能硬编码 0xF),否则 sh/lh 类用例比对失败。
  5. 测试终点:ibex 补丁版 riscv-test-env 的 PASS 路径写签名地址 0x8ffffff8(值[7:0]==1 为结果,位 8=0 通过);失败/异常路径才写 tohost(奇数值)。
  6. **虚假响应要求 SecureIbex=1**:`ibex_core.sv` 仅在 SecureIbex 时门控 LSU 响应(`g_check_mem_response`);SecureIbex=0 时核心信任总线协议,虚假响应(即使 err=0)会污染寄存器文件/触发虚假陷阱。UVM 同样在 `core_ibex_base_test` 中对非 secure 配置强制关闭虚假响应;本 tb 为 SecureIbex=0,`tb_env` 按同规则强制关闭(经 `secure_ibex_o` 常量端口)。
  7. **misaligned 标志必须来自层级探针**:SpikeCosim 依赖 `misaligned_first/second/first_saw_error` 配对拆分访问;这些标志只能从 LSU 内部信号(`handle_misaligned_d`/`addr_incr_req_o`/`lsu_err_d`,同 UVM tb)推导,且要在 **(req&&gnt) 地址相位**采样锁存,不能在请求断言沿采样(探针值尚未稳定)。
  8. **ecall 类测试(tohost)通过约定**:tohost 写 TESTNUM==1 为通过(scall),TESTNUM|1337 为失败;classify_test_result 按此判定。
  9. `+ibex_cocotb_zero_delays=1` 强制全零延迟,=0 强制随机延迟,缺省 50% 概率(UVM `zero_delay_pct` 语义);注意 pct 语义是"选择零延迟的百分比",100 才是强制零。
  10. `-include legacy_csr_compat.h` 供 riscv-tests 编译(sptbr→satp 等),新 xpack 工具链不再识别旧 CSR 别名。
  11. **NMI 与 WritebackStage=0 不兼容(UVM 同款边界)**:DUT 在 irq-only RVFI 事件(rvfi_ext_irq_valid)之后还会退休一条在途指令(事件的 `~instr_valid_id` 条件早于流水线彻底排空),而 SpikeCosim 的 NMI 模型在 set_nmi 的下一步立即陷入——失配。UVM 的 NMI 测试跑在 WritebackStage=1 配置上(riscv-dv 生成测试);本 tb 为 WB=0,故 irq_test 默认 `+ibex_cocotb_irq_nmi=0`(NMI handler 代码保留,供未来 WB=1 变体)。maskable 中断 + irq-only CMD_SET_MIP 路径已验收(irq_test:21362 条指令/334 次随机中断全部比对一致)。
  12. **irq_only 事件的洪泛**:rvfi_ext_irq_valid 是电平信号(事件后保持到 handler 首条指令退休),monitor 按周期去重(连续 irq_only 只保留第一个)。
- 下一动作:M2 验收(随机注入下 compliance 测试集通过)+ 之后 M3(gen/instr_gen.py)。
- 交接约束:比对核心必须复用 `dv/cosim` 的 `SpikeCosim` C++ 库(见 AOCI 索引中本文件的 S 字段),不得重写;不修改任何现有文件。
