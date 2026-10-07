# Ibex cocotb 验证方案

> 目标:在不修改任何现有文件的前提下,新增一套基于 cocotb 的验证环境,替代依赖商业 EDA 工具(VCS / Xcelium)的仿真环节,使功能仿真验证可以在开源工具链(Verilator + Spike + Python)上完整运行。
>
> 状态:M1-M5 已完成并验收(比对链路、内存/中断代理、随机指令生成、覆盖率模型、UVM 对拍);回归编排与覆盖率 merge 已落地(见 §9 末尾);跨会话状态见 §9。

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
- **第二轮 Ousterhout 设计审查 + P0/P1 重构已完成**(2026-10-07,23/23 重跑 PASS):配置唯一权威(`IbexCocotbConfig`,测试用 `dataclasses.replace` 自声明旋钮,plusarg 仅作覆盖);握手收口为 `mem_agent.TestHandshake`(含 `wait_for_result`,test_lib 的 Event 循环消失);IRQ 线数据化(`IrqLine` 表,agent 自清零自己的线);`DelayDist` 数据化;命令互斥改用 `cocotb.triggers.Lock`(FIFO 公平由框架保证);环境先装配后启动(`on_access` 进构造函数,消除接线窗口的静默丢失);任务用 `gather` 汇总失败;`_data_bus_busy` 单一总线忙状态;RVFI order 断档升级为 sticky 错误 + test_lib 断言;irq_only 改标准上升沿检测;crash dump 声明式解码并进入失配报告;死接口清理(`done_event`/`_outstanding`/`cfg.zero_delays`/公开 `pages`)。P2(M3 随行):拆 CosimChannel/Cosim/Scoreboard 三层、drain() 接口、结构化失配异常、NMI 检查条件化等。
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
  13. **tohost==1 判读冲突**:ecall/scall 类测试的通过约定是 tohost 写 1(失败 TESTNUM|1337),所以专用测试/生成程序的失败路径必须写**奇数且 ≠1** 的值(irq_test 各检查点 3/5/7/9,error_test/gen 用 3),否则失败被 classify 误判为 pass。
  14. **WB=0 上 maskable 中断取指不受 cosim 协议支持**:UVM 只在 WritebackStage=1 配置上验证中断流;WB=0 的 RVFI 在中断取指时重复呈现最后一条指令(同 order、valid 重断言约 17 拍)并产生取指伪影 trap 项,SpikeCosim 无对应物。irq_test 在 WB=0 上不写 mie、断言 handler 进入 0 次;中断取指与 irq-only 事件(CMD_SET_MIP)留给 WB=1 变体(同 NMI,坑 #10)。
- **M3 已完成**(2026-10-07,验收:23 条既有用例 + gen 多 seed 全部 PASS):
  - 新增 `gen/instr_gen.py`:riscv-dv 配比思路的随机指令生成器(ALU/ALUI/LOAD/STORE/BRANCH/MUL/CSR/JAL/JALR/压缩指令按权重混排,默认 stream_len=400 × iterations=10)。程序自校验:注入的非法指令(0x00000000/0xFFFFFFFF/0x7FFF 三种保证双模型一致 mtval 的 32 位字)必须逐一在专用非法段触发 mcause==2 陷阱、handler 跳过并计数,结尾核对 EXPECTED_ILLEGAL;到达握手(签名地址写 1)即证明整条流执行完毕。防假阳性保证:**流内所有跳转(branch/jal/jalr)只允许指向严格更靠后的标签**(不构成环,唯一的后向边是外层 bnez 循环,受 ITERATIONS 约束);**s11 为外层计数器,流内寄存器池排除之**;**load/store 全部经 `la t, data_pool` + 池内对齐偏移**(永不触碰握手地址或代码);**CSR 仅 mscratch**(riscv-dv 模板注释掉的计数器 CSR 曾造成 cosim 失配,mscratch 是唯一所有访问模式下双模型行为一致的 CSR);压缩指令用保守子集(c.nop/c.li/c.mv/c.addi 排除 rd=sp 与 imm=0 的 addi16sp/hint 边界,c.lw/c.sw 自带 `la s0, data_pool` 基址、x8-x15 编码池)。用法:`make sim TESTSUITE=gen TEST=<seed>`(seed 编码进文件名,换 seed 必然重新生成;编辑生成器同样触发重建)。
  - **P2 遗留项全部完成**:① 三层拆分 — 新 `py/cosim_channel.py`(CosimChannel:命令传输、opcode 唯一权威、握手超时、Lock 串行化)+ 新 `py/cosim.py`(Cosim:语义层,STEP/DSIDE/SETMIP 位域打包唯一权威在此,SV 侧仅命名常量解码;CosimError/CosimMismatchError)+ `py/scoreboard.py`(消费层,只做流程);② **drain() 追赶接口** — Scoreboard.drain(timeout_ns) 按调用时刻 retired_count 快照追赶(尾部无限循环会持续退休,不能等队列空),进度事件驱动,scoreboard 死亡时抛出结构化失配异常本身,test_lib 的轮询循环消失;③ **结构化失配异常** — CosimMismatchError(order/pc/insn/trap/errors 列表/crash_dump 解码 dict),错误字符串经新 DPI 命令 CMD_GET_ERROR_STR(a0=错误序号,a1=字序号,-1 取长度)按 32 位字传输(Verilator 的 DPI import 不支持 output 数组形参,故不用字符串缓冲端口),取前 16 条入异常、其余由 finish() 的 drain 打印;④ NMI 检查条件化 — irq_test.S 的阈值改 `NMI_EXPECT_MIN`(默认 0,WB=1 变体用 -DNMI_EXPECT_MIN=20 覆盖);⑤ ERROR_ADDR 单点定义 — Makefile `IBEX_ERROR_ADDR ?= 0xDEAD0000` 同时以 `-DERROR_ADDR=` 编译进 error_test.S 并以 `+ibex_cocotb_error_addrs=`(无 0x 前缀十六进制)传入 tb_env 解析合并;⑥ tb 的 trap_crash_dump 清零与分支统一 — `if (rvfi_valid) trap_crash_dump <= rvfi_trap ? crash_dump : '0`,monitor 逐项采样不再读到陈旧值。
  - **M3 期间发现并修复的环境级缺陷(重要)**:
    - *tohost==1 判读冲突(假阳性)*:ecall/scall 类测试的通过约定是 tohost 写 1,而 irq_test/error_test/生成程序的通用 `fail:` 路径也写 1 → 失败被误判为 pass。修复:专用测试/生成程序的失败路径改写 3(irq_test 各检查点分别写 3/5/7/9,handler_fail 仍写 (mcause<<1)|1)。此坑入列 #13。
    - *irq_test 的 checksum 检查数学上不成立*:中断期间 pass 累积 256×16 次 (acc^w)+1,重校验只跑 16 次,该交错运算不可幂等,两个值构造上就不可能相等 → M2 的 irq_test 自检从未通过(被 tohost==1 假阳性双重掩盖)。修复:重校验完整复现 256 轮同构累积(新增 s9 外层计数器)。
    - *WB=0 上 maskable 中断取指不受 cosim 协议支持(与坑 #11 同类)*:UVM 只在此 WritebackStage=1 的配置(opentitan)上验证中断流;WB=0 的 RVFI(RVFI_STAGES=1)在中断取指时会把最后一条指令以同 order 重复呈现约 17 拍(valid 重断言)并产生一个 pc/insn 为取指流水线伪影的 trap 项(intr=1、insn=0、pc 落在 handler 后零填充区),SpikeCosim 无对应物,喂进去必然失配(实测 csrw mie 后失配)。结论:irq_test 恢复诚实的 WB=0 语义 —— **不写 mie**(复位值 0,任何 maskable 中断都无法被采样),agent 的 raise 仅经 RVFI pre/post mip 流入 set_mip 逐步比对(该路径真实受验),测试断言 **handler 进入次数 == 0** 与 checksum 完整;真正的中断取指路径连同 irq-only 事件(CMD_SET_MIP)留给 WB=1 变体。M2 记录中"irq-only CMD_SET_MIP 路径已验收"的说法不实 —— 该路径从未真正触发(monitor 的 raw_irq_cycles 探针证实 0 次),已更正。
- **第三轮 Ousterhout 设计审查(sub-agent,基于 ousterhout-software-design skill)+ 第一批修复已完成**(2026-10-07,28/28 回归 PASS):审查确认三层拆分/单一权威/异常设计质量高,两个 P0 均为"静默漏检"类——**P0-1 命令协议跨语言双权威**(opcode 漂移响、bit 位漂移静默,会悄悄放松比对);**P0-2 teardown 比较错误被切断**(finish() 期间产生的失配只写 self.error 无人再读 → 假通过)。P1 六项(gnt 延迟分布与 UVM 不符且注释撒谎、data 端口互斥靠"无 await"、bin 路径双通道、.S 布局模板三处重复、wedge 无粘性失败、随机数不入日志)、P2 七项(死字段 intr、pass-through 注释、stream_len 措辞、IrqLine 字符串比较、生成器隐式状态、测试重复等)已记入审查报告。**第一批修复**:① P0-1 指纹版——两侧各自对 opcode+bit 位号表算 FNV-1a(py/cosim.py layout_fingerprint / tb layout_fingerprint 函数,CMD_INIT 返回指纹,强制奇数避免与 0 失败值冲突),任何漂移在 bring-up 立即失败(已负向验证:位号 6→7 被当场捕获);② P0-2——错误门槛移到 finish() 之后 + finish() 内部 re-raise;Cosim 加 sticky `_released`(release 后任何命令抛 "command issued after release",tb 守卫消息改为覆盖 before-CMD_INIT/after-CMD_RELEASE 两种真实状态);set_mip/notify_dside/release 校验返回值;scoreboard 比较循环改为 `cocotb.triggers.select(stop_event, monitor.get())`(注意:cocotb 2.1 弃用 First(task),`select` 是官方路径;首次实现用 First(task) 曾导致锁竞争放大、程序在仿真时间内变慢 4×)+ finish() 在 release 前等待 `_exited` 事件(消除在途 step 跨 release 的竞态,期间又发现并修复 select 的 item-won 分支缺 `_stopped` 重查);③ P1-3——bin 路径单通道(删 IBEX_COCOTB_BIN 环境变量,Python 改读 +ibex_cocotb_bin plusarg)。
  - **坑 #15(本轮暴露)**:irq_test 双遍 checksum(8192 次 load)在随机内存延迟模式下需约 6.4ms 仿真时间,超过原 5ms 的 RESULT_WRITE_TIMEOUT_NS → 约 50% 概率超时假失败(mem agent 的 zero_delay_pct=50 硬币翻转决定每次运行走零延迟还是随机延迟,此前多轮"偶发超时"均源于此,非 teardown 改动引入)。RESULT_WRITE_TIMEOUT_NS 已提至 20ms(实测最坏 ~6.5ms × 3 余量,仍能兜住真死锁)。
- **M4 已完成**(2026-10-07,28/28 回归 PASS):新增 `py/coverage.py` 覆盖率模型(cocotb-coverage 2.0,`pip3 install cocotb-coverage`;本机 Python 3.14 下 API 为 CoverPoint/CoverCross 类体装饰器 + coverage_db 单例,**无 crg/CoverageModel 模块**)。按 `coverage_plan.rst` 重建本 tb 可观测的点:指令类别(cp_id_instr_category,32 位非压缩译码表译自 UVM fcov + **RVC 象限级分类**,见坑 #16)、priv mode(RVFI mode + LSU m_mode 探针)、中断(NMI + mip 差分线,WB=0 上 take 桶恒空,见坑 #14)、debug req/mode、CSR 读/写(CSR 指令解码,地址按区块桶化:桶集为固定 elaboration 时 bins)、总线响应延迟(single/multi_cycle,agent 串行模型下 added_delay==0 即协议最小延迟)、misaligned 两半错误组合、trap 类别;交叉:priv×instr、interrupt/nmi×instr、csr×priv。**口径差异**:UVM fcov 在 ID/EX 级采样,本模型在退休点/总线响应采样(§7 风险 1 已接受);不可观测或配置不适用(SecureIbex=0/ICache=0/MemECC=0/WB=0:PMP、stall 分类、controller FSM、security countermeasures 等)的点列入 SKIPPED 表随报告打印原因。采样走已有观察者通道:monitor 新增 `on_retire` 回调、mem agent 新增 `BusEvent`/`on_bus_event` 回调(比对链路零改动);tb 新增引出 `rvfi_ext_expanded_insn*` 端口。报告:`coverage_db.report_coverage` 日志 + `out/coverage_<TESTSUITE>_<TEST>.xml`;coverage_db 为进程级单例,report 末尾重置 hit 计数(库无 reset API,依赖 `_hits` 私有字段)。开关:`+ibex_cocotb_cov=0`(cfg.coverage_enable 默认 True)。
  - **坑 #16(本轮暴露)**:RVFI 的 `rvfi_insn` 对压缩指令呈现 **16 位编码零扩展**(`{16'b0, instr_rdata_c_id}`),且 `rvfi_ext_expanded_insn_valid` 只对 IF 阶段真正展开的指令置位(绝大多数 RVC 指令不展开,c.nop 类直接执行)——所以"展开编码优先"策略不足以覆盖压缩指令,分类器必须自带 RVC 象限级分类(按象限把压缩编码归入其展开后的类别,UVM 在 ID 级看到的正是展开后指令,故口径一致);全零编码(陷阱伪影)特判为 Other 避免落入 c.addi4spn 的 ALU 桶。illegal 测试的 43 次 "Other" 假阳性即源于此(CODE_BEGIN/handler 宏以默认 RVC 编译)。另:expanded_valid 恒 0 在 norvc 测试(illegal.S)是正常现象。
- **第三轮审查第二/三批修复已完成**(2026-10-07,28/28 回归 PASS):① **P1-1 gnt 延迟分布对齐 UVM** — 核对 UVM 真实约束(`ibex_mem_intf_response_driver.sv` `send_grant` 的 gnt dist:min:/10、整个 [min+1:max-1]:/1、max:/1,中段权重用错位变量 `valid_pick_medium_speed_weight`;`ibex_mem_intf_response_seq_lib.sv` 的 rvalid dist:min:/5、[min+1:max/2-1]:/3、[max/2:max-1]:/1、max:/1),cocotb 的 GNT_DELAY 此前抄了 rvalid 的分段形态(注释撒谎属实);`DelayDist` 重构为 min/low_mid/high_mid/max 四段权重,两种分布精确表达;② **P1-2 data 端口单 owner** — `_maybe_spurious` 独立任务删除,spurious 响应移入 `_serve_data_port` 循环的空闲分支(该循环是 data 端口响应信号唯一 owner,`_data_bus_busy` 标志删除——互斥由结构保证而非标志轮询);③ **P1-4 .S 布局模板收敛** — 新建 `tests/sw/common.inc`(SIGNATURE/TOHOST define、`DIRECTED_TEST_ENTRY`、`DIRECTED_TEST_INIT`(PMP+mtvec)、`DIRECTED_TEST_TOHOST_ANCHOR` 四宏),irq_test.S/error_test.S 的重复块全部替换;坑:`#include` 经 cpp 预处理,注释里 `tests/sw/*.S` 的 `/*` 被 cpp 当块注释起始 → "unterminated comment"(common.inc 注释已注明此约束);④ **P0-1(a) 命令协议生成方案** — 新建 `py/cmd_defs.py`(opcode+位号表,单一权威)+ `gen/cmd_defs.py`(构建期渲染 `gen/out/cmd_defs.svh`);cosim_channel/cosim 的常量与指纹改从 cmd_defs import,tb 删手写 localparam 改 `include "cmd_defs.svh"` 宏引用;Makefile:CUSTOM_COMPILE_DEPS 钩子 + `-I gen/out` + mkdir(改了 VERILATOR_OPTS 必须 rm sim_build/Vtop.mk,坑 #3 同款);两侧同源,指纹降级为运行时 sanity;⑤ **P1-5 wedge 粘性失败** — mem_agent 的 dside 通知(on_access)失败转 sticky `agent.error`,`wait_for_result` 立即 raise 而非超时,test_lib 显式断言 `env.mem.error is None`;⑥ **P1-6 随机数入日志** — gnt/rvalid/spurious 延迟抽签、irq 线组合/保持/空闲周期均入 debug 日志。
- **M5 对拍已完成**(2026-10-07,29/29 回归 PASS):交付 `PARITY.md`(同语料结果对照 + 缺陷注入对照 + co-simulator 调用序逐项静态对照;本机无商业 EDA,UVM 侧证据取自源码与上游 CI)。parity 缺口补齐一项:**iside 取指错误通道** — `cmd_defs.py` 加 `CMD_SET_ISIDE_ERROR`(0x08,tb case 调 `riscv_cosim_set_iside_error`),mem agent 的指令端口现支持 `error_addrs`/`inject_error` 错误响应并记录 pending 地址(`consume_iside_error`),scoreboard 在 trap 项 step 前转发(UVM ifetch 队列等价);新 `tests/sw/iside_error_test.S` 端到端验证(mcause==1、mepc==ERROR_ADDR、handler 重定向 mepc 回测试代码——**错误地址+4 仍是毒地址,不能照抄 error_test 的跳过方案**,34/34 比对一致)。负向收获:改命令集后 Python 指纹(自动含新 opcode)与 tb 手写指纹数组(22 值未更新)当场失配,证明运行时 sanity 检查仍有价值;tb 指纹数组补 `CMD_SET_ISIDE_ERROR`(23 值)。剩余缺口记入 PARITY.md §4:mhpmcounters/ic_scr_key_valid(当前配置两侧等价)、debug_req 恒 0(CMD 位已就绪,缺 debug agent)、ifetch 逐次检查近似覆盖。
- **第四轮 Ousterhout 审查 + 修复已完成**(2026-10-07,29/29 回归 PASS):sub-agent 基于 ousterhout-software-design skill 审查本轮新代码,无新 P0;修复 4 个 P1 与 8 个 P2。① **P1-1 生成闭环** — 指纹顺序也由生成器渲染(`define CMD_LAYOUT_VALUES(_LEN)`),tb 的 `layout_fingerprint` 改为遍历生成数组,手写 23 值数组删除(第三处人工同步点消失,只剩 tb case,由 unique case+default $error 兜底);"can never drift" 乐观措辞修正为准确边界;Makefile 冗余 PYTHONPATH 删除;② **P1-2 iside 交接** — `iside_error_source` 必填(无默认值);消费改"每个非 irq_only item 取一次"+ 注入前校验 `item.pc & ~3 == addr`,不匹配 log 丢弃(UVM order 队列的 flush/陈旧项语义);agent 记录时对齐 `& ~0x3`(cosim 契约);③ **P1-3 inject_error 契约** — 一次性注入仅数据端口消费(`_take_error_for(addr, is_ifetch)`,取指不再抢走),docstring 同步;④ **P1-4 coverage 复位** — `_reset_hits()` 私有方法 + 注释记录"必须原地改私有字段"的推理(库无 reset API + 名字缓存 + size 只增,re-register 是错误做法)+ report 前 `_hits` 断言 + 新 `cocotb/requirements.txt`(cocotb==2.1.0、cocotb-coverage==2.0 精确锁版);⑤ P2 八项:三份 .S 的 `classify_test_result` 符号改 `TestHandshake.classify`;coverage 依赖改惰性导入(缺库只影响请求覆盖率的 run);报告路径改随 bin 同目录(OUT_DIR 天然携带)+ mkdir + 失败 try/except 不影响验证结论(覆盖是副产物);mem agent 观察者统一 `_notify`/`_notify_async` 保护(同步/异步两变体,失败均转 sticky error);irq_test 注释改"WB=1 变体需改本文件"并注明无开关(与 NMI_EXPECT_MIN 不同);priv mode 三处表示统一为 `priv_mode_name()` 字符串;test_lib 删不可达断言与未用 import;RvfiItem 注释标明 coverage-only 字段。
- 下一动作:全部 5 个里程碑完成。可选后续:debug agent + debug 测试、MHPM 计数通道(非零配置)、WB=1 变体(NMI/中断取指验证,坑 #11/#14)、riscv-arch-tests 语料接入。
- **回归与覆盖率 merge 已完成**(2026-10-07,wayfinder 地图 `.scratch/cocotb-regression-covmerge/`,t01/t02/t03;34/34 全量回归 PASS、负向验证通过):
  - `run.py` 回归编排器 + `testlist.yaml` 显式清单(rv32ui 7 + rv32mi 14 + sw 3 + gen seeds 1-10 = 34 条,与 PARITY 29/29 语料一致;新用例必须入清单才进回归)。流程:并行 build_test → elaboration 一次 → 复用同一 `sim_build/Vtop` 以不同 env/plusarg 并行跑(`-j` 默认 4,`--timeout` 默认 1200s,`--tests` 子串过滤);每测试日志 `out/logs/<suite>_<test>.log` + 独立 results.xml;汇总表 + 失败原因 + 非零退出码;失败不中断、全部跑完。Makefile 新增 `regression`/`regress-vars` 目标(run.py 的 TESTCASE/plusarg/工具路径一律经 regress-vars 取自 Makefile 单一权威)。实测:-j4 全量墙钟约 8s(串行约 29s)。
  - `merge_cov.py` 覆盖率合并(复用锁版 cocotb-coverage 2.0 的 `merge_coverage`,实测语义正确:hits 按 abs_name 求和、百分比重算):`out/coverage_*.xml` → `out/coverage_merged.xml` + 文本总表(`out/merge_report.txt`);`coverage_merged.xml` 永不作输入(防二次合并双计数);边界场景(无 XML/单 XML/不可解析)恒 exit 0——覆盖率是副产物,不参与判定。Makefile 独立 `merge_cov` 目标;`run.py` 汇总后自动调用(全量合并总体 27.95%)。
  - **坑 #17(本轮暴露,负向验证当场抓获)**:编译后的 sim 二进制在 cocotb 测试失败时**仍退出 0**(失败只记录在 results.xml),run.py 初版只信进程退出码 → 假 PASS。修复:PASS 判定改读 `COCOTB_RESULTS_FILE`(JUnit 格式的 failures+errors==0),仿真退出码仅作次信号;results.xml 缺失/不可解析一律按 FAIL。
  - **坑 #18**:cocotb 2.1 自带 makefile 以 `sim → regression → results.xml` 跑单仿真,`regression` 目标名被占用,同名全量 recipe 会污染每次 `make sim`。解法:recipe 用解析期 `ifeq ($(MAKECMDGOALS),regression)` 条件化(作为 sim 前置依赖时无 recipe);副作用:results.xml 过期时 `make regression` 会先跑一次默认单仿真(已注释在 Makefile)。
  - 负向验证:临时注入必败 error_test(注释其毒地址 plusarg,陷阱不触发) → 汇总正确标 FAIL、退出码非零、`out/logs/sw_error_test.log` 保留现场;注入(Makefile/testlist.yaml)逐字节还原(diff 校验)。
  - **回归规模与仿真 seed 调整(2026-10-07)**:gen 随机用例 10 → **100 条**(`testlist.yaml` seeds: 100,全量回归共 124 条);`run.py` 为每次仿真生成独立随机 `COCOTB_RANDOM_SEED`(`secrets.randbelow(2**31)`,写入日志首行、失败汇总附 seed 便于复现)。**坑 #19(本轮查证)**:cocotb 2.1 未设 `COCOTB_RANDOM_SEED` 时默认 `seed = int(time.time())`(墙钟秒),并行测试在同一秒启动会共享同一随机序列——此前"seed 都随机"并不成立。
  - **第五轮 Ousterhout 设计审查(sub-agent)+ 全部 P1 修复已完成(2026-10-07,124/124 回归 PASS)**:审查覆盖回归/merge/波形新代码,无 P0;4 个 P1 全部修复 — ① **P1-1 MODULE/TOPLEVEL 双权威**:run.py 不再硬编码 `tests.test_core`/`ibex_cocotb_tb`,改由 regress-vars 输出;② **P1-2 regress-vars 协议 + make 启动开销**:改 `KEY='value'` 每行一条(shlex 解析,值含 `=`/空格不再误切),build+query 合并为每测试一次 make 调用(124 次 vs 原 248 次);③ **P1-3 XML 命名双权威**:新建 `py/cov_paths.py`(零依赖,`coverage_xml_path(out_dir, suite, test)`),test_lib(写侧)与 run.py(merge 侧)同源;④ **P1-4 COMPILE_ARGS 人工重建坑**:新增编译输入 hash stamp(`sim_build/.compile_args.sha1`,入 CUSTOM_COMPILE_DEPS),改 COMPILE_ARGS/CPPFLAGS/VERILOG_SOURCES 后自动重新 elaboration,彻底消灭"手动 rm sim_build/Vtop.mk"(WAVES 切换实测自动重建)。P2 同步落地:RunResult dataclass、LZ4_PREFIX 可覆盖(默认 `brew --prefix lz4` 回退)、merge 用 PYGPI_PYTHON_BIN(与仿真侧同库版本)、OUT_DIR/SIM_BUILD 经 regress-vars 传递;顺带观察:merge 报告文件列表折行。P2 遗留:failure_reason 排除清单(可接受,未做)。
- 交接约束:比对核心必须复用 `dv/cosim` 的 `SpikeCosim` C++ 库(见 AOCI 索引中本文件的 S 字段),不得重写;不修改任何现有文件。
