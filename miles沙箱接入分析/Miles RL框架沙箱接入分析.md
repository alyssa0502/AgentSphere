# Miles RL 框架沙箱接入分析

> **分析对象**:[radixark/miles](https://github.com/radixark/miles) main 分支(源码快照 2026-11-06)
> **问题**:AgentRL 场景下,在 miles 训练流程中引入沙箱 —— 沙箱位于哪个流程?怎么接?
> **方法**:实读源码 + 官方文档(仓库内 docs/)+ Miles v0.1 论文(arXiv 2609.08368),全部结论给出 `文件:行号` 级出处(见第七章附录)。
> **版本**:合订本,概述 + 七章;图例:🟨 沙箱 / 🟩 Rollout 侧 / 🟦 Trainer 侧 / ⬜ 外部或数据。

## 目录

- [概述与核心结论](#概述与核心结论)
- [第一章 Miles 框架总览](#第一章-miles-框架总览)
- [第二章 训练流程与沙箱位置(核心答案)](#第二章-训练流程与沙箱位置核心答案)
- [第三章 沙箱接入点精确定位](#第三章-沙箱接入点精确定位)
- [第四章 沙箱生命周期与工程要点](#第四章-沙箱生命周期与工程要点)
- [第五章 三种内置沙箱形态对比](#第五章-三种内置沙箱形态对比)
- [第六章 AgentSphere 对接方案](#第六章-agentsphere-对接方案)
- [第七章 附录:代码索引、事实来源与参考链接](#第七章-附录代码索引事实来源与参考链接)

---

# 概述与核心结论

## 一句话结论

**沙箱只存在于 Rollout(轨迹采样)阶段**:它位于 miles 三层 rollout 插件中最内层 —— **agent function 的"agent-环境循环"** 里,作为工具调用 / 任务实例的**执行后端**。训练(Trainer)与权重同步(Weight Sync)两大阶段完全不接触沙箱;沙箱对训练唯一的"贡献物",是环境在容器内打出并经 `Sample.reward` 回流的**分数**。

```
┌─────────────────────────── miles RL 主循环 ───────────────────────────┐
│                                                                       │
│  ① Rollout(轨迹采样)          ② Training(训练)       ③ Weight Sync │
│  ┌──────────────────────────┐   ┌──────────────────┐   ┌───────────┐ │
│  │ SGLang 引擎(生成 token)  │   │ Megatron/FSDP     │   │ Broadcast │ │
│  │ Session Server(TITO)     │   │ RL loss / 优势估计 │   │ P2P       │ │
│  │ ★ agent function ★       │──▶│ optimizer step    │──▶│ Disk-delta│ │
│  │    ★ ★ ★ 沙箱在这里 ★ ★ ★ │   └──────────────────┘   └───────────┘ │
│  └──────────────────────────┘          │                              │
│         ▲           │                  └── 新权重 ──▶ 回到 ①          │
│         └── 完成轨迹经缓冲区回流 ──┘                                  │
└───────────────────────────────────────────────────────────────────────┘
```

## 全景架构:沙箱悬在整个训练系统之外

把 GPU 池、进程和沙箱基础设施画在同一张图上,可以一眼看出:**沙箱不在 Ray 集群里,它是一个外部服务**,只被 rollout 侧的 agent function 调用:

```mermaid
flowchart TB
    subgraph RAY["Ray 集群"]
        subgraph TR["🟦 训练侧 GPU 池"]
            T["Trainer<br/>Megatron / FSDP<br/>RL loss · 优势估计 · 优化器"]
        end
        subgraph RO["🟩 Rollout 侧 GPU 池"]
            RT["Miles Router<br/>会话亲和 · KV cache 复用"]
            SG["SGLang 引擎 ×N"]
            SR["Session Server(TITO)<br/>精确记录 token / logprob"]
            AF["Agent Function<br/>每 episode 一个子进程"]
        end
    end
    subgraph SBX["🟨 沙箱基础设施(集群外)"]
        P1["沙箱 Provider<br/>E2B / Daytona / Modal / Novita / AgentENV / AgentSphere"]
        C1["容器 / 微虚机池<br/>create → exec → verify → delete"]
        P1 --- C1
    end
    T <-->|"② 完成轨迹(TITO token 序列 + reward)"| RO
    T -.->|"③ 权重同步 Broadcast / P2P / Disk-delta"| SG
    AF <-->|"OpenAI chat 多轮(沙箱 observation 只是消息)"| SR
    SR <--> SG
    AF ==>|"★ 沙箱 SDK:create / exec / delete"| P1
    classDef train fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef roll fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class T train
    class RT,SG,SR,AF roll
    class P1,C1 sbx
```

要点补全:

- **沙箱是第四根独立的"轴",不是第四层插件**。三层插件(agent / generate / rollout function)负责 agent-环境循环、token 记录、分组与编排;沙箱 provider(E2B / Daytona / Modal / Novita / AgentENV)在连接器**内部**供给容器(`docs/user-guide/environments.md:31`)。
- **两种生命周期**:miles 官方食谱是**每 episode 一个容器,用后销毁**;也支持指向**单个长驻环境服务器复用**(`docs/user-guide/sandbox-providers.md`)。
- **同步(`train.py`)与完全异步(`train_async.py --fully-async`)** 两种编排下,沙箱都只在 Rollout 侧,但异步模式下沙箱快慢直接影响 staleness 与丢弃率(见第一章 §4、第二章 §2)。

## 章节指南

| 章节 | 内容 | 适合谁读 |
|---|---|---|
| 概述 | 一句话结论 + 全景架构 | 所有人 |
| 第一章 Miles 框架总览 | 三段循环、进程拓扑、同步/异步流水线 | 需要背景的人 |
| 第二章 训练流程与沙箱位置 | 一次 GRPO 迭代逐步时序,逐步标注沙箱参与(**核心答案**) | 所有人 |
| 第三章 沙箱接入点精确定位 | 三层插件模型、agent function 契约、代码级接入点清单 | 要动手接沙箱的人 |
| 第四章 沙箱生命周期与工程要点 | create→exec→verify→teardown 全周期、凭证安全、超时取消、观测 | 运维/平台侧 |
| 第五章 三种内置沙箱形态对比 | 进程内轻量沙箱 / 本地 Docker / 云沙箱 provider + 选型决策树 | 做选型的人 |
| 第六章 AgentSphere 对接方案 | 用 AgentSphere 当 miles 的沙箱后端:零代码方案 + 验证清单 + 风险 | 落地执行 |
| 第七章 附录 | 全部 `文件:行号` 索引、事实来源标注、参考链接 | 回溯核查 |

---

# 第一章 Miles 框架总览

## 1. Miles 是什么

Miles 是 **RadixArk(SGLang / LMSYS 团队)的企业级 LLM/VLM 后训练 RL 框架**,与清华 THUDM 的 slime 同源、协同演进;官方称其面向万亿参数 MoE 的生产级训练。核心组合:**SGLang 做推理采样(rollout)+ Megatron-LM / FSDP / torchtitan 做训练**,内置 GRPO、GSPO、REINFORCE++、PPO 等优势估计器。

关键工程特性(论文 §2/§4 + 仓库 `docs/developer/architecture.md`):

- **Session Server(TITO, Token-In-Token-Out)**:agent 用 OpenAI 兼容 chat 接口多轮交互,miles 保留引擎产出的**精确 token ID、logprobs、路由专家**,组装成训练样本——trainer 看到的就是 policy 实际采样的 token,消除 train-rollout mismatch。
- **Miles Router**:FastAPI 代理;key-based routing 把同一会话粘在持有其 KV cache 的引擎上(论文报告 96% prefix cache 命中)。
- **R3(Rollout Routing Replay)**:记录每个 token 的 MoE 专家路由,训练时精确 replay,避免路由漂移。
- **多训练后端**:Megatron / FSDP2 / torchtitan;多硬件(CUDA/TPU,`miles/platforms/`)。

## 2. RL 主循环:三阶段

```mermaid
flowchart LR
    subgraph A["① Rollout(SGLang 侧 GPU)"]
        direction TB
        R1["SGLang 引擎生成"] --> R2["Session Server 组装 TITO 样本"]
        R2 --> R3["环境/沙箱执行工具"]
        R3 --> R1
    end
    subgraph B["② Training(训练侧 GPU)"]
        direction TB
        T1["RL loss(策略梯度)"] --> T2["optimizer step"]
    end
    subgraph C["③ Weight Sync"]
        direction TB
        W1["Broadcast / P2P / Disk-delta"]
    end
    A ==>|"完成轨迹(含 reward)"| B
    B ==> C
    C -.->|"新权重"| A
    classDef roll fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef train fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef sync fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    class R1,R2,R3 roll
    class T1,T2 train
    class W1 sync
```

三阶段不必按序执行。同步交替会让两侧硬件互相等待(空泡);miles 提供**完全异步模式**(分离 GPU 池,两侧同时推进,有界缓冲区衔接)。注意:**colocated(训练与推理共享 GPU)部署时,miles 拒绝启动完全异步**。

**权重同步三种传输方式**(论文 §4):

| 方式 | 机制 | 适用 |
|---|---|---|
| Broadcast(默认) | NCCL 广播到每个 rollout rank | 通用 |
| P2P(§4.2) | RDMA 直写 rollout rank 内存,大集群比 broadcast 快 85%+ | 大规模分离部署 |
| Disk-delta(§4.3) | 只把变化字节写共享文件系统,各 rollout host 本地 patch | 跨机/慢网络 |

## 3. 进程拓扑(一次运行 = 三类进程,包在 Ray 里)

```
Ray 集群
├── Trainer ranks(Megatron 组,1+ 个)          ← 不接触沙箱
├── Rollout(N 个 SGLang server + Miles Router) ← 沙箱挂在这一侧的 agent function
└── Data Source(RolloutDataSource,提示词缓冲)  ← 不接触沙箱
```

出处:`docs/developer/architecture.md:9-32`。SGLang server 是独立 HTTP 服务;Router 负责分发、保元数据(R3)、健康检查;DataSource 是 trainer 持有的 Python 对象,读 prompt JSONL 并充当 rollout↔training 之间的缓冲。带沙箱基础设施的完整拓扑见概述的全景架构图。

## 4. 完全异步流水线(论文 §2.2,源码 `miles/rollout/fully_async_*.py`)

两种编排直观对比——**同步模式下 GPU 互相等(空泡),异步模式靠有界缓冲解耦**:

```mermaid
flowchart TB
    subgraph SYNC["同步模式(train.py):交替推进,互相等待"]
        direction LR
        s1["🟩 Rollout<br/>(沙箱在跑,几分钟/条)"] --> s2["⏳ GPU 空泡<br/>(trainer 等 rollout)"]
        s2 --> s3["🟦 Training step"]
        s3 --> s4["🟪 Weight Sync"] --> s1
    end
    subgraph ASYNC["完全异步(train_async.py --fully-async):两侧持续推进"]
        direction LR
        a1["🟩 Rollout Worker 池<br/>引擎保满:完成一条即补一条<br/>(sample 粒度,默认)"] ==>|"放入 group"| BUF
        BUF["🟪 有界缓冲区<br/>容量 = batch × k<br/>staleness 超限 ⇒ 丢弃"] ==>|"取出 batch"| a2["🟦 Trainer<br/>连续 step,不等 rollout"]
        a2 -.->|"权重更新到在飞请求:<br/>retract(默认)/ leave"| a1
    end
    classDef roll fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef train fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef buf fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef idle fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    class s1,a1 roll
    class s3,a2 train
    class s4,BUF buf
    class s2 idle
```

- **引擎保满(§2.2.1)**:后台 worker 维持在飞轨迹数上限;补位规则两种——group 粒度(整组完成才补)与 **sample 粒度(默认,单条结束即释放名额)**。
- **有界数据缓冲区(§2.2.2)**:容量为训练 batch 的整数倍;接口只有三个操作:放入 group、取出 batch、上报指标。三种丢弃:
  1. **生成超时** —— 到达时检查;
  2. **filter 拒绝**(如整组 reward 相同、无 advantage 信号)—— 到达时检查;
  3. **staleness 超限** —— 取出时检查;staleness = trainer 当前权重版本 − 组内最旧权重版本(故意取悲观值)。
- **可观测(§2.2.3)**:每步上报 `queue_size`、`avg/max_staleness`、`buffer_avg/max_staleness`、`aborted_groups_filtered`、`stale_groups_filtered`(指标前缀 `rollout/fully_async/`)。queue 恒 0 ⇒ rollout 是瓶颈;恒满 ⇒ trainer 是瓶颈。
- **权重更新与在飞请求(§4.4)**:三选项 abort / leave / **retract(默认,回滚后以新权重续跑)**;启用 session server 时禁用 abort。
- **异步评估(§2.2.4)**:三模式——共享 rollout 引擎(暂停生成)、专用评估 GPU 池(checkpoint snapshot)、外部后端(只读 checkpoint 目录);评估分数严格绑定权重版本;失败只跳过不中断训练。

## 5. 与沙箱的关系(预告)

- Trainer / Weight Sync / DataSource / Router:全部**不接触**沙箱。
- 沙箱的全部活动都发生在 Rollout 侧、且具体发生在 **agent function 内部**(细节见第二章、第三章)。
- 完全异步模式下,**沙箱的交付延迟与并发上限直接决定 rollout 吞吐**,进而决定 staleness、丢弃率与 GPU 利用率——这是"沙箱放哪个流程"之外最值钱的工程结论。

---

# 第二章 训练流程与沙箱位置(核心答案)

本章把一次 GRPO 迭代拆成逐步时序,**每一步标注沙箱是否参与**,并对比同步 / 完全异步两种编排下沙箱位置的差异。

## 1. 先看定位:一条流水线上的 10 步,沙箱只占中间 4 步

```mermaid
flowchart LR
    s1["1<br/>取 prompt"] --> s2["2<br/>建 TITO 会话<br/>拉起 agent 子进程"]
    s2 --> s3["3<br/>SGLang 逐轮生成"]
    s3 --> s4["4<br/>sandbox.create"]
    s4 --> s5["5<br/>工具循环 exec"]
    s5 --> s6["6<br/>verifier 评分"]
    s6 --> s7["7<br/>teardown 销毁"]
    s7 --> s8["8<br/>reward 回流"]
    s8 --> s9["9<br/>优势估计<br/>loss · step"]
    s9 --> s10["10<br/>权重同步"]
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f,stroke-width:3px
    classDef roll fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef train fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    classDef sync fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    class s4,s5,s6,s7 sbx
    class s1,s2,s3,s8 roll
    class s9 train
    class s10 sync
```

🟨 **第 4–7 步是沙箱的全部活动范围**;第 9、10 步(训练、同步)与沙箱零交互。

## 2. 同步路径:一次 GRPO 迭代的完整时序

时序骨架来自 `docs/developer/architecture.md:83-101`,沙箱环节按 `examples/swe-agent-harbor-docker` + `miles/rollout/generate_hub/agentic_tool_call.py` 实码展开:

```mermaid
sequenceDiagram
    autonumber
    participant T as Trainer(Megatron)
    participant DS as DataSource
    participant RL as Rollout Worker
    participant SS as Session Server(TITO)
    participant SG as SGLang 引擎
    participant AF as Agent Function(子进程)
    participant SB as 沙箱(E2B/Docker/…)

    T->>DS: get_samples(N) 取提示词
    T->>RL: generate(prompts)  ⛔训练侧到此为止
    RL->>SS: POST /sessions 建 TITO 会话(每条轨迹一个)
    RL->>AF: call_agent_function(subproc 拉起子进程)
    rect rgb(255, 244, 230)
        AF->>SS: OpenAI chat(第 1 轮)
        SS->>SG: input_ids 生成
        SG-->>AF: 助手消息 + tool_calls(含精确 token/logprob)
        AF->>SB: ★ sandbox.create(每 episode 一个容器)
        loop agent-环境循环(工具轮)
            AF->>SB: exec 工具/命令
            SB-->>AF: observation
            AF->>SS: 追加消息(chat)
            SS->>SG: 增量生成(复用最深 checkpoint)
            SG-->>AF: 下一轮输出
        end
        AF->>SB: verifier 跑测试 → reward
        AF->>SB: ★ teardown(用后销毁)
    end
    AF-->>RL: reward + 元数据(合并进 Sample.metadata)
    RL-->>T: 样本组(含 token 级 TITO 序列)
    T->>T: RM/reward 钩子 → 优势估计 → RL loss → optimizer step
    T->>SG: weight sync(新权重)
```

### 逐步标注:沙箱在哪几步?

| 步 | 动作 | 沙箱? | 说明 |
|---|---|---|---|
| 1 | DataSource 取 prompt | ❌ | 纯数据读取 |
| 2 | 建会话 / 拉起 agent 子进程 | ❌ | TITO 会话与 token 化是 miles 自己的 |
| 3 | SGLang 逐轮生成 | ❌ | 推理引擎,不属于沙箱 |
| 4 | **sandbox.create** | ✅ | **沙箱第一次出现**:每 episode 一个容器 |
| 5 | **agent-环境循环内的工具执行** | ✅ | **沙箱主战场**:exec、文件读写、浏览器等都在容器内 |
| 6 | **环境内评分(verifier)** | ✅ | Harbor 的 test.sh 在容器里跑出 reward |
| 7 | **teardown** | ✅ | 用后销毁(`EnvironmentConfig(delete=True)`) |
| 8 | reward/元数据回流 `Sample.reward` | ❌ | 此后沙箱已退场 |
| 9 | 优势估计 / loss / optimizer step | ❌ | Trainer 纯 GPU 计算 |
| 10 | 权重同步 | ❌ | NCCL/RDMA/磁盘 |

> 结论:**沙箱全部集中在第 4–7 步,即 Rollout 阶段的 agent function 内部**;对训练侧而言,沙箱是一个黑盒服务,进出只有 reward 和元数据。

## 3. 完全异步路径:沙箱位置不变,影响变大

`train_async.py --fully-async`:trainer 与 rollout 使用分离 GPU 池持续推进,经**有界缓冲区**衔接(机制图见第一章 §4)。沙箱仍只在 rollout 侧,但出现三个新的工程耦合:

1. **沙箱慢 ⇒ rollout 慢 ⇒ staleness 高 ⇒ 丢弃多**。staleness = trainer 权重版本 − 轨迹最旧权重版本;轨迹在缓冲区里等得越久越容易被 stale 丢弃(论文 §2.2.2)。
2. **沙箱并发上限 ⇒ 在飞轨迹上限**。sample 粒度补位(默认)意味着每条轨迹各自占一个沙箱名额;沙箱 provider 配额不足时,补位逻辑会让 episode 反复 create→失败→refill,表现为"静默烧 GPU"(miles 在 `credentials.py:187` preflight_sdk 的注释里专门点名这种失败模式)。
3. **oversampling abort 的反向控制**。trainer 采样够了会 abort 在飞生成;外部沙箱/agent server 若不知情,会继续跑到自己的上限。miles 提供 **abort hook**(`--custom-agent-function-path` 模块可选 `async def abort(args)`)让 agent fn 通知外部后端停止——`swe_agent_function.py:159` 的实现就是向 Harbor agent server 发 `/flush` 释放容器(`docs/user-guide/agentic-rollout.md:115-133`)。

## 4. 沙箱在"流程全景"里的位置(含离线阶段)

| 阶段 | 是否用沙箱 | 内容 |
|---|---|---|
| 离线·任务构建 | ✅(可选) | 任务镜像/模板预构建(TB2 自带预构建镜像);E2B Cloud 模板以非 root 构建,任务 Dockerfile 需 root 的 RUN 层会失败(`docs/user-guide/sandbox-providers.md:49`) |
| 离线·难度过滤/数据生成 | ✅(可选,自建) | miles 不管数据生成;可用沙箱预跑参考解(sandbox_smoke 的 golden 就是"在沙箱里执行任务自己的参考解",`scripts/sandbox_smoke/run.py:25`) |
| **Rollout(轨迹采样)** | ✅ **(正典位置)** | create → 工具循环 → verifier → teardown |
| Training | ❌ | 纯 GPU |
| Weight Sync | ❌ | NCCL/RDMA/共享盘 |
| 评估 | ✅ | 异步评估三模式里,凡走 rollout 的评估同样在沙箱内跑(分数严格绑定权重版本,论文 §2.2.4) |

## 5. 常见误判澄清

- **"沙箱是不是 SGLang 的一部分?"** 不是。SGLang 只产 token;工具执行在 agent function 调用的外部沙箱里,两者之间隔着 Session Server 的 OpenAI 兼容接口。
- **"reward 是不是 trainer 算的?"** 通常不是。沙箱场景下 reward 多在**环境内**产生(Harbor verifier、τ-bench 场景校验),经 agent fn 返回的 dict 合入 `Sample.metadata`/`Sample.reward`(`agentic_tool_call.py:177-183`);miles 的 `rm_hub` 规则奖励主要用于无环境 RLVR。
- **"KV cache 亲和会不会连沙箱也粘住?"** 不会。key-based routing 只粘 SGLang 引擎;沙箱粘性由 agent fn 自己持有(一个 episode 一个沙箱句柄,子进程生命周期即 episode 生命周期)。

---

# 第三章 沙箱接入点精确定位

## 1. 总模型:三层插件 × 一根沙箱轴

miles 把 rollout 栈做成**三层嵌套插件**,连接器只替换其中一层(`docs/user-guide/environments.md:38-55`):

```mermaid
flowchart TB
    subgraph L3["rollout function(最外层):分组 · 编排 · 数据源"]
        subgraph L2["generate function:TITO 会话 · 样本收集"]
            subgraph L1["🟨 agent function(最内层):★ 沙箱挂在这里 ★"]
                AF["agent-环境循环"]
            end
        end
    end
    SESS["🟩 Session Server → SGLang<br/>(token 由 miles 记录)"]
    PROV["🟨 沙箱 Provider<br/>E2B / Daytona / Modal / Novita / AgentENV"]
    AF <-->|"OpenAI chat:沙箱 observation 只是又一条 message"| SESS
    AF ==>|"★ 沙箱 SDK:第四根独立的轴,不是第四层"| PROV
    classDef roll fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class SESS roll
    class PROV sbx
```

| | Agent function(最内层) | Generate function | Rollout function(最外层) |
|---|:---:|:---:|:---:|
| 接入旗标 | `--custom-agent-function-path` | `--custom-generate-function-path` | `--rollout-function-path` |
| Agent-环境循环 | ✓ | ✓ | ✓ |
| 轨迹与 token 记录(TITO) | ○ miles | ✓ 自理 | ✓ 自理 |
| Reward 通路 | ○ miles | ○ miles | ✓ |
| 数据源 / 分组编排 | ○ | ○ | ✓ |
| 模型 / 权重更新 / 优势 / 优化器 | ○ | ○ | ○ |

外部生态的落层:Harbor、NeMo Gym、OpenEnv → agent function;HUD、Strands、τ-bench → generate function;Prime Intellect Verifiers → 整个 rollout function。

**沙箱 provider 是独立的第四根轴,不是第四层**。官方原话(environments.md:31):*"Sandbox providers are a different axis: they provision the task containers **inside** a connector rather than occupying a rollout layer."* 支持的 provider:AgentENV(Firecracker 微虚机、E2B API 兼容)、Daytona、E2B、Modal、Novita(兼容矩阵见 `docs/user-guide/sandbox-providers.md:12-18`)。

## 2. Agent function 契约(接沙箱的唯一必写代码)

`docs/user-guide/agentic-rollout.md:51-62` + `miles/rollout/generate_hub/agentic_tool_call.py:12-29`:

```python
async def run_agent(
    base_url: str,        # 已含 /sessions/<id>,不要再拼
    prompt,               # OpenAI messages 列表(勿手动套 chat template)
    request_kwargs: dict, # 采样参数(ChatCompletionRequest 兼容)
    metadata: dict,       # 含 max_seq_len、session_server_instance_id 等
    **kwargs,
) -> dict | None:         # 返回 dict 会合并进每条样本的 metadata(如 reward/eval_report)
```

沙箱接入就写在这个函数里:进函数 → `sandbox.create` → 循环(模型↔工具)→ verifier 出分 → 关沙箱 → 返回元数据。模板实现:`examples/swe-agent-harbor-docker/swe_agent_function.py`(agent-server 模式)与 `examples/experimental/harbor/harbor_agent_function.py`(in-process 云沙箱模式)。

## 3. 代码级接入点清单(全部实读核对)

### 3.1 Provider 注册与凭证(miles 核心,唯一需要"登记"的地方)

| 接入点 | 位置 | 内容 |
|---|---|---|
| Provider 注册表 | `miles/rollout/agentic/credentials.py:29` | `PROVIDER_CREDENTIALS`,每后端一条;**加 provider=加一条登记项,不改连接器代码**(`docs/developer/adding-a-sandbox-provider.md:7`) |
| 凭证供给契约 | `credentials.py:135` `sandbox_key_supply` | launcher **只转发 key 文件的 PATH**,绝不转发密钥值(ray runtime_env 会被明文打进日志) |
| 地址变量转发 | `credentials.py:95` `forward_address` | endpoint 按值转发,但**拒绝含 `@` 的 URL**(防凭证借 userinfo 夹带) |
| SDK 预检 | `credentials.py:187` `preflight_sdk` | 启动时验 SDK 在场与版本下限(如 e2b≥2.12),防"每个 episode 静默失败→refill 空转烧 GPU" |
| 密钥解析 | `credentials.py:235` `resolve_provider_api_key` | worker 侧:环境变量优先,回落 key 文件 |
| launcher 总装 | `credentials.py:258` `provision_provider` | 上面四件事的一次性调用入口 |

现有登记项:daytona / e2b / modal / novita(credentials.py:30-92);e2b 条目自带 `E2B_API_URL`、`E2B_SANDBOX_URL` 等转发变量——**"任何讲 E2B API 的服务器都能接"**(`docs/user-guide/sandbox-providers.md:31`),这是第三方沙箱(如 AgentSphere)零代码接入的钥匙。

### 3.2 Agent 调用与隔离(miles 核心运行时)

| 接入点 | 位置 | 内容 |
|---|---|---|
| Agent 调用入口 | `miles/rollout/agentic/agent_function.py:95` `call_agent_function` | `subproc`(默认)/ `inline` 两种模式 |
| 每 episode 独立子进程 | `agent_function.py:109` | 慢步骤/崩溃只影响单 episode——否则一个慢沙箱 SDK 调用会卡住整个事件循环,拖到其他 episode 的沙箱请求超时(模块 docstring :1-9) |
| 取消宽限 | `agent_function.py:33` `_CANCEL_GRACE_S=120s` | episode 被取消后,给 agent 120s 自己关沙箱 |
| 清理上限 | `agent_function.py:35` `_LEFTOVER_CLEANUP_S=600s` | 调用结束后遗留清理线程最多等 10 分钟 |
| 孤儿防护 | `agent_function.py:207` `_die_with_parent` | PR_SET_PDEATHSIG:rollout 进程死 ⇒ 内核直接杀 agent 子进程,沙箱关不掉也有兜底 |
| 子进程上报通道 | `agent_function.py:84` `report_to_caller` | agent 子进程可向 rollout 进程实时发结构化消息(沙箱健康上报就走这条) |
| 跨进程并发限流 | `miles/rollout/agentic/rollout_limits.py` | `rollout_semaphore`/`rollout_lock`:subproc 模式下模块级 Semaphore 失效,用它限并发 |
| 外部后端急停 hook | `swe_agent_function.py:159` `abort` | oversampling abort 时向 Harbor agent server 发 `/flush`,取消在飞 trial、释放容器 |

### 3.3 Wrapper 层(把 agent fn 与 TITO、观测缝起来)

| 接入点 | 位置 | 内容 |
|---|---|---|
| Agentic 生成函数 | `miles/rollout/generate_hub/agentic_tool_call.py:54` `generate` | 建 TITO 会话→调 agent fn→收集样本 |
| 运行模式旗标 | `agentic_tool_call.py:203` | `--custom-agent-function-mode`(默认 subproc) |
| 长度限制下传 | `agentic_tool_call.py:209` + `docs L195` | `--max-seq-len` 同时发给外部环境,让沙箱侧可提前止损 |
| 沙箱健康上报 | `agentic_tool_call.py:101`(on_child_reports=`push_sandbox_reports`) | 链路:`dashboard/hooks.py:400-416`(subproc 走 report_to_caller,inline 直推)→ `dashboard/collector.py:215` → `dashboard/sandbox_health.py`(面板) |
| 环境耗时记账 | `agentic_tool_call.py:186-191` | agent 返回 `agent_metrics.total_tool_time` → `Sample.non_generation_time`,吞吐核算扣除非生成时间 |
| 元数据合并 | `agentic_tool_call.py:177-183` | v1 driver 侧合并 agent 返回 dict;`session_id` 由 tracer 独占,agent 不得覆盖 |

### 3.4 参考实现(examples,接沙箱时抄哪个)

| 实现 | 位置 | 模式 | 沙箱 |
|---|---|---|---|
| SWE agent + Harbor + Docker | `examples/swe-agent-harbor-docker/swe_agent_function.py` | agent-server(独立服务跑 Harbor `Trial.run`) | 本地 Docker daemon,单机 |
| Harbor in-process | `examples/experimental/harbor/harbor_agent_function.py` | agent function 直调 Harbor 库 | **云 provider**:HARBOR_ENV_TYPE 直通(:299-307),`EnvironmentConfig(delete=True)` 用后销毁(:338),verifier reward 提取(:415-427),密钥经注册表解析(:286) |
| retool 多轮工具 | `examples/retool_v2/tool_sandbox.py` | generate 内工具注册表 | **进程内轻量沙箱**(非容器):正则黑名单+rlimit 子进程(:87-291) |
| OpenEnv / NeMo Gym / HUD / τ-bench | `examples/experimental/{openenv,nemo-gym,hud,tau-bench}` | 各落一层 | 云 provider 或场景服务器 |

### 3.5 质量门槛(miles 官方验收路径)

`docs/developer/adding-a-sandbox-provider.md:14-29`:新 provider 四步——①注册表登记项;②**golden 冒烟**(真实平台跑参考解,`scripts/sandbox_smoke/run.py`,PASS 条件 `reward==1.0 && exit_status=="Submitted"`,:146-152;无 GPU、无模型);③GPU rollout e2e(`tests/e2e/agentic/test_harbor_rollout.py`,2 卡);④文档表格加行。golden 是唯一能证明"镜像解析→create→exec→verifier→teardown 全链路"的测试。

## 4. Reward 的三条通路(沙箱分数如何进入训练)

```mermaid
flowchart LR
    subgraph SBX["🟨 沙箱内"]
        V["verifier / test.sh<br/>环境内评分"]
    end
    AFN["agent fn 返回 dict<br/>reward · exit_status · eval_report"]
    META["Sample.metadata / Sample.reward"]
    RM["reward 通路<br/>--custom-rm-path 提取<br/>或 rm_hub 规则奖励"]
    ADV["🟦 miles 侧<br/>组内 advantage(GRPO 等)"]
    V --> AFN --> META --> RM --> ADV
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    classDef train fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    class V,AFN,META,RM sbx
    class ADV train
```

1. **环境内评分(沙箱场景主流)**:verifier 在容器内跑 → agent fn 返回 `{"reward": …, "exit_status": …, "eval_report": …}` → 合入 `Sample.metadata` → 经 `--custom-rm-path` 自定义 RM 钩子提取(`swe_agent_function.py:151-156` docstring 明说)。
2. **agent fn 直出**:返回 dict 里的 reward 直接参与合并(v2 走服务端合并)。
3. **miles 内置规则奖励**(`miles/rollout/rm_hub/`):无环境的单轮 RLVR(math/ifbench 等);retool 示例把工具调用次数做成奖励塑形(`tool_sandbox.py:365-382`)。

组级 advantage(GRPO 等)永远在 miles 侧算——环境只管给绝对分。

## 5. TITO 与沙箱的分工(容易混淆)

```mermaid
flowchart LR
    subgraph MILES["🟩 miles 负责(碰 token)"]
        TK1["chat 模板渲染<br/>增量 tokenize"]
        TK2["保留引擎精确 token ID / logprob"]
        TK3["checkpoint 复用<br/>只 tokenize 新增后缀"]
        TK4["对齐 · 截断 · 组装训练样本"]
        TK1 --> TK2 --> TK3 --> TK4
    end
    subgraph SBXW["🟨 沙箱只负责(碰环境)"]
        E1["文件系统 / shell exec"]
        E2["进程 · 网络 · 浏览器"]
        E3["verifier 评分"]
        E1 --> E2 --> E3
    end
    AF["agent function"] --> MILES
    AF --> SBXW
    classDef roll fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class TK1,TK2,TK3,TK4 roll
    class E1,E2,E3 sbx
```

- 沙箱产生的 observation 只是又一条 message;**token 化、token ID 保留、logprob 记录全部归 Session Server**。规则:prompt 保持 messages 列表、不要 `--apply-chat-template`、不要设 `input_ids`/`routed_experts_start_len` 等控制字段(agentic-rollout.md 警告框)。
- 会话历史:v1 线性(可回滚一轮),v2 实验性 append-only 树(支持 fork/压缩类 harness);`--session-message-matcher` 决定重放消息是否算同一条。
- 结论:**沙箱只负责"环境状态与工具执行",永远不碰 token**——这是 miles 能保证 trainer 看到 on-policy token 的关键。

---

# 第四章 沙箱生命周期与工程要点

## 1. 两种生命周期模式

官方明确支持两种(`docs/user-guide/sandbox-providers.md` / `adding-a-sandbox-provider.md`):

| 模式 | 做法 | 适用 |
|---|---|---|
| **Per-episode(官方食谱)** | terminal-bench 食谱:每个 episode 建一个容器,**用后销毁** | 任务间需完全隔离(SWE/TB 类);miles 参考实现默认此模式(`EnvironmentConfig(delete=True)`,`harbor_agent_function.py:338`) |
| **长驻复用** | 指向单个长驻环境服务器,反复使用 | 环境初始化极贵、或任务本身无状态可重置(τ-bench 类场景服务器) |

## 2. Per-episode 单沙箱全周期(与 miles 各环节的对应)

```mermaid
flowchart TB
    A["episode 开始<br/>agent 子进程被拉起"] --> B["镜像解析<br/>task.toml → 预构建镜像/模板"]
    B --> C["★ sandbox.create<br/>(热池命中则 <1s,冷启动可达 20s+)"]
    C --> D["初始化/bake<br/>注入任务文件、agent harness(如 OpenCode)"]
    D --> E["agent-环境循环<br/>exec 命令 / 读写文件 / 每轮经 TITO 会话打模型"]
    E --> F["★ verifier 跑 test.sh<br/>容器内产出 reward"]
    F --> G["agent fn 返回元数据"]
    G --> H["★ teardown:delete=True 销毁"]
    H --> I["子进程退出(120s 取消宽限内关沙箱)"]
    E -.->|episode 被取消| X["SIGTERM→agent 自行清理<br/>超 120s 强杀,600s 兜底清线程"]
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f,stroke-width:2px
    class C,F,H sbx
```

各环节的 miles 责任归属:①②⑤⑥ 由连接器/agent fn 负责;③④⑧ 由沙箱 provider 负责;取消语义由 `agent_function.py`(120s/600s/PDEATHSIG)兜底。Daytona 特例:被杀 worker 走不到 Harbor teardown,而 Daytona 默认 auto_stop=0 永不回收 → miles 显式设 `auto_stop_interval_mins` 防泄漏(`harbor_agent_function.py:310-318`)——**接任何新 provider 都要回答"谁来回收孤儿沙箱"**。

## 3. 凭证与安全模型(miles 的硬约束)

凭证流转一图流——**密钥的值从头到尾不过 launcher 的转发链**:

```mermaid
flowchart LR
    K["🔑 API key<br/>(E2B_API_KEY 等)"] --> F["key 文件<br/>~/.config/e2b/api_key<br/>或 K8s Secret 挂载"]
    F -->|"① launcher 只转发 PATH"| W["rollout worker"]
    W -->|"② worker 自己读文件/自己的 env"| SDKV["e2b SDK"]
    SDKV -->|"③ create/exec"| SBX["🟨 沙箱 API"]
    L["launcher"] -.->|"地址类变量按值转发<br/>(含 @ 的 URL 直接拒绝)"| W
    classDef key fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class K,F key
    class SBX sbx
```

- **密钥永不进 launcher 转发链**:launcher 只把 key **文件路径**交给 worker(`credentials.py:135`);理由是 ray `runtime_env` 会被明文打进 driver 日志并持久化到 job 元数据。
- **地址按值转发、但拒绝 `@`**:`https://user:token@host` 形式的 URL 直接 ValueError(`credentials.py:95-114`)。
- **多机布置要求**:key 文件必须在**每个 rollout worker 可读**的位置——共享文件系统、同路径文件、或 K8s Secret 挂载;worker 自己的环境变量(平台注入/单机继承)也行(`sandbox-providers.md:20-27`)。
- **启动即失败优于中途失败**:SDK 缺失/版本过低在 launcher 侧就 raise(`preflight_sdk`,`credentials.py:187-216`),避免"每个 episode 都在沙箱 create 失败 → 组丢弃 → refill 永动机"。

## 4. 超时与取消语义速查

| 参数/机制 | 默认 | 出处 | 含义 |
|---|---|---|---|
| `AGENT_TRIAL_TIMEOUT` | 7200s(agent-server)/ 1200s(smoke) | `swe_agent_function.py:27`、`sandbox_smoke/run.py:22` | 单 episode 沙箱侧总时长上限,可环境变量覆盖 |
| `_CANCEL_GRACE_S` | 120s | `agent_function.py:33` | 取消后 agent 关沙箱的宽限 |
| `_LEFTOVER_CLEANUP_S` | 600s | `agent_function.py:35` | 遗留清理线程上限 |
| `--max-seq-len` | — | `agentic_tool_call.py:209` | token 上限,同时下传环境用于 early stop |
| oversampling abort hook | 可选 | `agentic-rollout.md:115-133` | 通知外部沙箱/agent server 急停(flush) |
| PDEATHSIG | Linux | `agent_function.py:207` | rollout 进程死 → 内核杀 agent 子进程 |
| Daytona auto-stop | 显式设置 | `harbor_agent_function.py:310-318` | 孤儿沙箱回收 |

取消与清理的时间线(miles 给"关不掉的沙箱"准备的三层兜底):

```mermaid
flowchart LR
    X["episode 取消/超时"] --> Y["SIGTERM<br/>agent 自行清理(关沙箱)"]
    Y -->|"≤120s 成功"| Z["子进程退出 ✓"]
    Y -->|">120s 仍卡"| K["SIGKILL 强杀"]
    R["rollout 进程崩溃"] -.->|"PDEATHSIG(内核层)"| K
    L["调用结束后的遗留线程<br/>(如正在 close 的沙箱)"] -.->|"最长等 600s"| E["进程退出"]
    classDef kill fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    class K kill
```

## 5. 观测与瓶颈定位

- **沙箱健康面板**:`miles/dashboard/sandbox_health.py`(`SandboxReport/SandboxRow/SandboxHealth`);agent 子进程经 `report_to_caller → push_sandbox_reports`(hooks.py:400-416)→ collector 汇聚。健康/错误样本数、每沙箱耗时可在 dashboard 看。
- **非生成时间**:`agent_metrics.total_tool_time` → `Sample.non_generation_time`(`agentic_tool_call.py:186-191`),吞吐核算会扣除——**沙箱耗时被官方当作一等公民指标**。
- **流水线水位**:完全异步指标 `queue_size`、`avg/max_staleness`、`aborted_groups_filtered`、`stale_groups_filtered`(论文 §2.2.3)。经验法则:queue 恒 0 → rollout(常即沙箱)瓶颈;恒满 → trainer 瓶颈。

## 6. 性能陷阱与对策清单

| # | 陷阱 | 现象 | 对策 |
|---|---|---|---|
| 1 | 沙箱冷启动慢 | 在飞轨迹数起不来,GPU 空泡 | 选支持**热池/预热**的 provider;模板常驻 |
| 2 | provider 并发配额 < rollout 并发 | create 排队→超时→组丢弃→refill 空转烧 GPU | 并发沙箱数 ≈ rollout 并发 ×(1+oversample),配额对齐;`rollout_limits` 限流兜底 |
| 3 | 磁盘配额 | 部分后端按"并发数×每沙箱盘"计总量(Daytona 章节明确提醒) | 用 `HARBOR_OVERRIDE_STORAGE_MB` 调小每沙箱盘 |
| 4 | 模板/镜像未预构建 | 首 episode 建 20 分钟模板 | 离线预构建;TB2 用官方预构建镜像;注意 E2B Cloud 模板非 root 构建限制 |
| 5 | 事件循环被同步步骤卡住 | 全体 episode 沙箱请求超时 | 保持默认 `subproc` 模式;慢步骤出子进程 |
| 6 | 孤儿沙箱 | 配额被僵尸容器吃满 | 用后销毁 + provider 侧 auto-stop/回收策略 + abort hook flush |
| 7 | 密钥泄漏 | 凭证进日志 | 只传 PATH;URL 拒 `@`;K8s Secret |
| 8 | 评估污染训练 | eval 分数混入训练权重版本 | 用官方异步评估三模式,分数严格绑版本(论文 §2.2.4) |

## 7. 与自建基础设施的接口(给平台侧的三句话)

1. miles 对沙箱的全部要求 = **一套 SDK 兼容 API + 可达的 endpoint + 可读的 key 文件路径**;集群里把这三样送到 rollout worker 就完成接入。
2. miles 不管理沙箱资源池——**池化、预热、配额、回收都是 provider 侧的事**(官方只做生命周期调用)。
3. 排障顺序:launcher 日志(provision_provider 的回显会打印实际使用的 endpoint)→ sandbox_smoke golden → sandbox 面板 → `rollout/fully_async/*` 指标。

---

# 第五章 三种内置沙箱形态对比

miles 仓库里实际存在三种"沙箱"形态,隔离强度、吞吐上限、运维成本完全不同。先看三种形态的架构差异:

```mermaid
flowchart TB
    subgraph FA["形态 A:进程内轻量(retool)"]
        direction LR
        w1["rollout worker"] --> ps["PythonSandbox 子进程<br/>正则黑名单 + rlimit + 超时杀"]
    end
    subgraph FB["形态 B:本地 Docker + agent-server"]
        direction LR
        w2["rollout worker"] -->|"/run HTTP(长连接)"| asv["agent server<br/>Harbor Trial.run"]
        asv --> dk["本地 Docker 容器<br/>任务环境 + verifier"]
    end
    subgraph FC["形态 C:云沙箱 provider(in-process)"]
        direction LR
        w3["rollout worker"] ==>|"沙箱 SDK"| cp["E2B / Daytona / Modal / Novita<br/>AgentENV / AgentSphere"]
        cp --> vm["容器 / 微虚机池<br/>create → exec → verify → delete"]
    end
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class ps,dk,vm,cp sbx
```

## 1. 形态 A:进程内轻量沙箱(非容器)

**代表**:`examples/retool_v2/tool_sandbox.py`(retool 多轮工具调用训练)

- 实现:`PythonSandbox`(:87)= 危险模式正则黑名单(:106-159,禁 os/subprocess/open/eval/双下划线等)+ 白名单 import + `resource.setrlimit` 内存上限(:212)+ `subprocess` 拉独立 python3 跑(:261)+ 超时 kill + 临时目录清理。
- 并发:进程内全局 `asyncio.Semaphore(32)`(:31),外加内存水位三档清理(:67-84)。
- reward:数学 DAPO 打分 + 工具使用奖励塑形(:365-382)。

**定位**:算题类 RLVR 的"够用就行"。隔离靠正则,可被绕过,只防误不防恶;执行都在 rollout worker 本机。

## 2. 形态 B:本地容器 + Agent-Server(单机 Docker)

**代表**:`examples/swe-agent-harbor-docker`

- 架构:miles agent function(`swe_agent_function.py`)→ HTTP 调一个独立的 **agent server**(:105 `AGENT_SERVER_URL`,默认 localhost:11000)→ server 内跑 Harbor `Trial.run()` → Harbor 在**本地 Docker daemon** 起任务容器、跑 agent、跑 verifier → 返回 reward/eval_report。
- 为什么要独立 server:Harbor trial 是长阻塞调用(agent 可以跑几小时,超时 7200s),从 miles 的事件循环里解耦;每条在飞 trial 独占一条长连接(keepalive 常驻,:57-84)。
- 急停:`abort` hook 向 server 发 `/flush`(:159-197)释放容器;server 侧支持 `HARBOR_ADMIN_SECRET` 鉴权。
- 连接池:默认不设上限(`AGENT_SERVER_MAX_CONNECTIONS` 可调,:37-54)——上限低于 rollout 并发会静默排队。

**定位**:SWE-bench 类任务的最快起步路径;一台大内存 Docker 机的吞吐天花板明显。

## 3. 形态 C:云沙箱 Provider(E2B 兼容 API 系)

**代表**:`examples/experimental/harbor`(in-process,无中间 server)

- 实现:agent function 直调 Harbor 库;`HARBOR_ENV_TYPE` 直通 EnvironmentType(:299-307),provider 密钥经 `PROVIDER_CREDENTIALS` 注册表解析(:286-295),`EnvironmentConfig(delete=True)` 用后销毁(:338)。
- 官方验证过的 provider(`docs/user-guide/sandbox-providers.md`):**AgentENV**(自建 Firecracker 微虚机、讲 E2B API)、**Daytona**、**E2B**、**Modal**、**Novita**。
- miles 对 provider 的全部假设:一套 SDK + endpoint + key 文件;**池化/预热/配额是 provider 自己的事**。

**定位**:规模化 agent 训练的正解;弹性吞吐,按沙箱·秒计费或自建微虚机池。

## 4. 对比表

| 维度 | A 进程内轻量 | B 本地 Docker | C 云 Provider |
|---|---|---|---|
| 隔离强度 | ★(正则+rlimit) | ★★★(容器) | ★★★★(容器/微虚机,跨机) |
| 启动延迟 | ~0(子进程) | 秒级(Docker 起容器) | 冷启动 20s+,**热池可 <1s** |
| 吞吐上限 | 高(本机进程) | 单机 Docker 并发上限 | provider 配额(可横向扩) |
| 出网依赖 | 无 | 无 | rollout worker → provider API |
| 多机集群 | 不适合 | 不适合(单机 daemon) | ✅ 原生 |
| 接入代码量 | 写工具注册表 | 起 server + agent fn | **零代码**(env 变量 + key 文件) |
| abort/急停 | 进程内直接管 | `/flush` | SDK close + provider 回收策略 |
| 典型任务 | 数学/代码题 RLVR | SWE-bench 单机 | SWE/TB/任意容器任务规模化 |
| 仓库参照 | `retool_v2` | `swe-agent-harbor-docker` | `experimental/harbor` |

## 5. 选型决策

```mermaid
flowchart TB
    q1{"任务只是执行一段受信代码算个分?"}
    q1 -->|是| fa["形态 A:进程内轻量<br/>(只防误不防恶,勿用于不可信样本)"]
    q1 -->|否,需要真实环境| q2{"只有一台机器 / 先跑通?"}
    q2 -->|是| fb["形态 B:本地 Docker + agent-server"]
    q2 -->|要规模化·多节点·长期| q3{"公有 provider 可用?"}
    q3 -->|是| fc1["形态 C:E2B / Daytona / Modal / Novita"]
    q3 -->|数据/合规要自建| fc2["形态 C:自建 AgentENV(Firecracker)<br/>或任何讲 E2B API 的自有平台<br/>★ AgentSphere 属于这一类"]
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class fa,fb,fc1,fc2 sbx
```

> 三种形态可以混用:混合数据集可按 sample 指定不同 agent function(`agentic_tool_call.py:70` 支持每样本 `agent_function_path`)。

与第六章的衔接:AgentSphere 走形态 C 路线,复用 miles 的 `e2b` provider 登记项。

---

# 第六章 AgentSphere 对接方案

> 目标:让 miles 的 rollout 用 AgentSphere 作为沙箱 provider(形态 C 路线)。
> 前提假设:AgentSphere 暴露 **E2B 兼容 API**(本机既有验证记录:2026-11-03 新 key 验证通过、活跃区 cn-southwest-2、模板 c4bf4922、50 并发 p99 0.95s、出网=模板级配置)。所有"本机环境参数"均来自既有验证记录,接入前建议复核。

## 1. 核心结论:零代码接入

miles 的 `e2b` provider 明确支持"**E2B Cloud,或任何讲 E2B API 的服务器**"(`docs/user-guide/sandbox-providers.md:31`),endpoint 通过环境变量切换(`credentials.py:57` 的 forward 列表)。因此对接 = **改环境变量 + 放好 key 文件**,不改 miles 一行代码:

```bash
# 控制面 + 数据面都指向 AgentSphere(按实际地址替换)
export E2B_API_URL=http://<agentsphere-endpoint>       # 控制面
export E2B_SANDBOX_URL=http://<agentsphere-endpoint>   # 数据面
# key 按契约放文件(路径由 launcher 转发,值不进命令行/日志)
mkdir -p ~/.config/e2b && echo <key> > ~/.config/e2b/api_key

# 连接器选 harbor、后端选 e2b
HARBOR_ENV_TYPE=e2b
```

注意:若经 AgentSphere 网关(既有记录中的"路由三件套头 + 本地反代 18789"模式),**需先验证 miles/e2b SDK 的 HTTP 客户端能否携带网关要求的自定义头**;miles 转发链只认 `E2B_API_URL`/`E2B_SANDBOX_URL` 等白名单变量(`credentials.py:57`),自定义头不在其中。可行路径:a) 网关侧放行 rollout worker 网段;b) 本地反代注入头(miles 连反代地址)。——**此项待验证**。

## 2. 部署布置矩阵

| 部署形态 | key 供给 | endpoint 供给 | 说明 |
|---|---|---|---|
| 单机开发 | `export E2B_API_KEY` 或 `~/.config/e2b/api_key` | 同上 export | worker 继承 launcher 环境 |
| Ray 多机 | 每台 worker 同路径放 key 文件,或共享 FS | launcher 环境(按值转发) | launcher 只传 **PATH**(`credentials.py:135`) |
| K8s 集群 | Secret 挂载到 rollout pod(`~/.config/e2b/api_key`) | Pod env 注入 `E2B_API_URL/E2B_SANDBOX_URL` | 官方文档要求 key 文件在 rollout worker 可读处(`sandbox-providers.md:24-27`) |
| AgentSphere 侧 | — | 网关/直连 | 沙箱**出网**是模板级配置,与上述入方向无关 |

启动自检:launcher 会回显实际使用的 endpoint(`credentials.py:277-279` `provision_provider` 的 target echo)——**看启动日志确认打的是 AgentSphere 而不是 E2B Cloud**。

## 3. 容量规划(关键,算错就烧 GPU)

**并发沙箱需求 ≈ rollout 在飞轨迹数 × (1 + oversample 裕量)**

- 在飞轨迹数由 rollout batch/group 配置决定;sample 粒度补位(默认)意味着每条轨迹各自独占一个沙箱。
- AgentSphere 已验证容量:50 并发、p99 create 0.95s、热池(create 前 20s 恒定预热)30 并发打不空、交付 p50 0.86s(既有记录)。
- **配额不足的失败模式**(miles 侧实证描述,`credentials.py:187` 注释):每个 episode 在 create 处失败 → 样本 abort → 组丢弃 → rollout refill 永动机,**GPU 满载空转**。宁可将 rollout 并发压到配额以内,先小后大。
- 磁盘:参照 Daytona 章节的教训(`sandbox-providers.md:59-60`),确认"并发数 × 每沙箱盘"不超总量;Harbor 侧可用 `HARBOR_OVERRIDE_STORAGE_MB` 调每沙箱盘。

热池匹配度评价:miles 的 per-episode 生命周期(建→用→毁)恰好需要"create 快 + 销毁干净"的 provider,AgentSphere 热池模式与峰值并发对齐后是理想组合。

## 4. 已知坑位清单(接入前逐条核对)

| # | 坑 | 依据 | 对策 |
|---|---|---|---|
| 1 | **e2b SDK ≥ 2.12** 强制(miles `preflight_sdk` 检查) | `credentials.py:54`(旧版把模板名当 `alias` 发,自建服务器不读 → 首次 bake 裸 400) | 按官方提示装新版:`pip install -e '<miles>[e2b]'` |
| 2 | 模板引用方式 | 同上 | 用模板 **ID**(如既有模板 c4bf4922);AgentSphere `GET /templates` 是 404,任何"列模板"逻辑都不可用——miles 现在不列模板,直接 create(template_id),无影响 |
| 3 | 沙箱内需要 pip/git/拉依赖 | 既有记录:出网=**模板级**配置 | 任务所需依赖**预装进模板**,或选用已开网的模板;不要指望 episode 内临时开网 |
| 4 | E2B Cloud 模板非 root 构建限制 | `sandbox-providers.md:49-51` | 任务 Dockerfile 需 root 的 RUN 层放镜像/模板里预做 |
| 5 | 长任务超时 | `AGENT_TRIAL_TIMEOUT` 默认 7200s(`swe_agent_function.py:27`) | 按任务 p99 调;同时给 `--max-seq-len` 让环境早停 |
| 6 | 网关自定义头 | 本方案 §1 | 未验证;备选:网关放行/本地反代注入 |
| 7 | 孤儿沙箱 | miles 只按契约调 close/delete | AgentSphere 侧配自动回收(参照 miles 给 Daytona 设 auto-stop 的做法) |
| 8 | 多机 key 下发 | `sandbox-providers.md:24-27` | Secret/共享 FS;绝不用带 key 的 URL(会被 `forward_address` 拒绝且进日志) |

## 5. 分阶段验证路径

```mermaid
flowchart LR
    p0["阶段 0<br/>连通性<br/>curl 控制面/数据面"] --> p1["阶段 1<br/>golden 冒烟<br/>无 GPU · 几分钟<br/>reward==1.0 且 Submitted"]
    p1 --> p2["阶段 2<br/>2 卡 e2e<br/>TITO + reward 回流"]
    p2 --> p3["阶段 3<br/>小规模训练<br/>在飞 ≤30(热池安全区)<br/>盯面板与指标"]
    p3 --> p4["阶段 4<br/>放量到配额 ~80%<br/>观察 create p99 与丢弃率"]
    classDef sbx fill:#fde68a,stroke:#d97706,color:#78350f
    class p1,p2,p3,p4 sbx
```

各阶段明细:

```text
阶段 0  连通性:rollout 机器 curl AgentSphere 控制面/数据面;确认 DNS/代理
阶段 1  golden 冒烟(无 GPU、无模型,几分钟):
        python scripts/sandbox_smoke/run.py --connector harbor --backend e2b
        PASS 判据:reward == 1.0 且 exit_status == "Submitted"(run.py:146-152)
        —— 这一关证明:镜像解析→create→exec→verifier→teardown 全链路通
阶段 2  GPU e2e(2 卡):
        HARBOR_ENV_TYPE=e2b 跑 tests/e2e/agentic/test_harbor_rollout.py
        —— 证明:harness、session server、TITO 记录、reward 回流全链路通
阶段 3  小规模训练(压并发:在飞轨迹 ≤ 30,对应热池实测安全区):
        跑 examples/experimental/harbor 的 launcher,盯 dashboard 沙箱面板 +
        rollout/fully_async/{queue_size, staleness, *_filtered}
阶段 4  放量:并发爬升到 provider 配额的 ~80%,观察 create p99 与丢弃率
```

目标拓扑:

```mermaid
flowchart LR
    subgraph Miles集群["Miles 训练集群(K8s/Ray)"]
        T["Trainer(Megatron/FSDP)"]
        SG["SGLang 引擎 ×N"]
        RL["Rollout Worker<br/>agent function(子进程/episode)"]
        T <-->|"weight sync"| SG
        RL --> SG
    end
    RL ==>|"e2b SDK: create/exec/verify/delete<br/>E2B_API_URL · E2B_SANDBOX_URL"| GW["AgentSphere API/网关"]
    GW --> P["🟨 沙箱热池(模板 c4bf4922)<br/>50 并发 · p99 0.95s"]
    P -.->|"出网=模板级配置"| NET["外网(按需)"]
```

## 6. 工作量评估

| 事项 | 工作量 | 性质 |
|---|---|---|
| miles 侧代码改动 | **0 行** | 纯环境变量 + key 文件 |
| AgentSphere 侧 | 确认 E2B API 兼容面(create/exec/端口转发/文件)已有验证记录 | 复核 |
| 验证四阶段 | 阶段 1 半天内,阶段 2 需 2 卡环境 | 按 §5 清单 |
| 任务模板制作 | 每个 RL 任务一套模板/镜像(依赖预装、测试脚本就位) | 主要人力在这 |
| 网关头兼容(如走网关) | 待验证 | 唯一不确定点 |

**总结**:miles 把"沙箱在哪个流程"这个问题本身工程化了——它被压进 rollout 侧 agent function 的一个标准契约里,沙箱平台只要讲 E2B API 就能零代码挂进来;真正的工作量在**任务模板化**和**并发容量对齐**上。

---

# 第七章 附录:代码索引、事实来源与参考链接

## 1. 关键代码索引(仓库相对路径,均已实读)

### 沙箱接入核心

| 关注点 | 位置 | 说明 |
|---|---|---|
| Provider 注册表 | `miles/rollout/agentic/credentials.py:29` | `PROVIDER_CREDENTIALS`(daytona/e2b/modal/novita) |
| e2b 条目(endpoint 转发、SDK≥2.12) | `credentials.py:42-59` | `E2B_API_URL`/`E2B_SANDBOX_URL` 等 |
| 密钥 PATH 转发契约 | `credentials.py:135` `sandbox_key_supply` | 值不过 launcher 链 |
| `@` 凭证夹带拒绝 | `credentials.py:95` `forward_address` | URL 含 `@` 即 ValueError |
| SDK 启动预检 | `credentials.py:187` `preflight_sdk` | 防 episode 级静默失败空转 |
| worker 侧密钥解析 | `credentials.py:235` `resolve_provider_api_key` | env 优先,回落文件 |
| launcher 侧总装 | `credentials.py:258` `provision_provider` | 含 endpoint 回显 |
| Agent 调用(subproc/inline) | `miles/rollout/agentic/agent_function.py:95` | `call_agent_function` |
| 取消宽限 120s / 清理 600s | `agent_function.py:33,35` | 沙箱关闭的兜底时限 |
| PDEATHSIG 孤儿防护 | `agent_function.py:207` | 父进程死则内核杀 |
| 子进程→主进程上报 | `agent_function.py:84` `report_to_caller` | 沙箱健康上报通道 |
| 跨进程并发限流 | `miles/rollout/agentic/rollout_limits.py` | `rollout_semaphore/rollout_lock` |
| Agentic 生成 wrapper | `miles/rollout/generate_hub/agentic_tool_call.py:54` | TITO 会话 + 样本收集 |
| 模式旗标 | `agentic_tool_call.py:203` | `--custom-agent-function-mode` |
| `--max-seq-len` 下传环境 | `agentic_tool_call.py:209` | 环境侧早停 |
| 沙箱健康上报链 | `agentic_tool_call.py:101` → `miles/dashboard/hooks.py:400-416` → `dashboard/collector.py:215` → `dashboard/sandbox_health.py` | 面板数据流 |
| 非生成时间记账 | `agentic_tool_call.py:186-191` | `total_tool_time`→`non_generation_time` |

### 参考实现

| 文件 | 要点 |
|---|---|
| `examples/swe-agent-harbor-docker/swe_agent_function.py` | agent-server 模式;:94 `run`,:105 `AGENT_SERVER_URL`,:151 返回 reward 元数据,:159 `abort`(flush) |
| `examples/swe-agent-harbor-docker/run.py` / `run-glm47-flash-agentic-async.py` / `run_glm52_lora_tb2_daytona.py` | 生产 launcher(daytona 版可直接对照云沙箱布置) |
| `examples/experimental/harbor/harbor_agent_function.py` | in-process 云 provider;:286 密钥解析,:299-307 `HARBOR_ENV_TYPE` 直通,:310-318 Daytona 回收,:338 `delete=True`,:415-427 verifier→reward |
| `examples/retool_v2/tool_sandbox.py` | 进程内轻量沙箱;:87 `PythonSandbox`,:294 `ToolRegistry`,:365 reward 塑形 |
| `scripts/sandbox_smoke/run.py` | golden 冒烟;:22 超时 1200s,:25 golden=参考解,:146-152 PASS 判据 |
| `examples/experimental/{openenv,nemo-gym,hud,tau-bench,strands_sglang}` | 其他连接器落层实例 |

### 流程与架构

| 文件 | 要点 |
|---|---|
| `docs/developer/architecture.md` | :9-32 进程拓扑,:44-74 包布局,:83-101 同步 GRPO 请求生命周期,:156-163 三十分钟阅读路径(train.py→sglang_rollout→loss→router→weight_update) |
| `docs/user-guide/environments.md` | :22-29 连接器×落层表,:31-34 **沙箱=轴不是层**,:47-55 三层能力矩阵 |
| `docs/user-guide/agentic-rollout.md` | :51-62 agent fn 契约,:92-133 subproc 模式与 abort hook,:136-133 TITO 语义,:171-196 v1/v2 会话与 max-seq-len,:248-266 message matcher |
| `docs/user-guide/sandbox-providers.md` | :12-18 provider×连接器矩阵,:31-51 E2B(含自建服务器、非 root 构建),:59-60 Daytona 磁盘配额,:64-80 Modal |
| `docs/developer/adding-a-sandbox-provider.md` | :14-29 新 provider 四步,:34-45 golden vs rollout-e2e 两道测试 |
| `train.py` / `train_async.py` | 同步 / 完全异步入口 |
| `miles/rollout/fully_async_rollout.py`、`fully_async_data_buffer.py` | 异步流水线实现 |
| `miles/rollout/rm_hub/` | 内置规则奖励(math/ifbench/gpqa…),`__init__.py` 为 rm_type 分发点 |

## 2. 事实来源分级标注

- **[代码]**:本次直接阅读仓库源码(快照 2026-11-06,本地 `%TEMP%\miles_repo\miles-main`)。所有 `文件:行号` 均属此类。
- **[文档]**:仓库内 `docs/**`(与代码同源,通常随码更新)。
- **[论文]**:Miles v0.1(arXiv 2609.08368)WebFetch 摘要——三阶段循环、P2P 快 85%+、disk-delta、完全异步的有界缓冲/三种丢弃/staleness 定义/指标名/评估三模式/§4.4 在飞请求处理(retract 默认、session server 禁 abort)、96% prefix cache 命中。细节如需引用到生产文档,建议对论文原文复核。
- **[本机记录]**:AgentSphere 环境参数(cn-southwest-2、模板 c4bf4922、50 并发 p99 0.95s、热池 20s 预热、交付 p50 0.86s、出网=模板级、GET /templates 404、网关三件套头)来自本机既往验证记录,非 miles 仓库内容。

## 3. 本地快照

源码快照位置:`%TEMP%\miles_repo\miles-main`(22.8MB zip 解压,含 docs/tests/examples 全量)。上游同步:`git` 未装,后续可用 zip 覆盖或安装 git 后 clone。

## 4. 参考链接

| 资源 | 链接 |
|---|---|
| miles 仓库 | https://github.com/radixark/miles |
| miles 文档站 | https://miles.radixark.com/docs |
| LMSYS 发布博客 | https://www.lmsys.org/blog/2025-11-19-miles |
| Miles v0.1 论文 | https://arxiv.org/html/2609.08368v1 |
| TITO 设计博客(No Token Left Behind) | https://lmsys.org/blog/2026-05-13-no-token-left-behind/ |
| slime(同源框架) | https://github.com/THUDM/slime |
| Harbor | https://github.com/harbor-framework/harbor |
| AgentENV(自建 E2B API 服务) | https://github.com/kvcache-ai/AgentENV |
| Terminal-Bench-2 | https://github.com/laude-institute/terminal-bench-2 |
| E2B | https://e2b.dev/ · Daytona: https://www.daytona.io/ · Modal: https://modal.com/ |
