# 用一个项目学会 Agent 工程

这条路线围绕一个可回答的问题展开：**Agent 调用了哪些工具，哪里失败了，修改后是否真的更可靠？**

AgentTrace Lab 是用来练习这个问题的本地 Python 项目。它把工具执行过程变成可以检查、测试和比较的记录。运行本仓库的离线示例不需要模型 API 密钥。先用确定的输入检查工程行为，再接入模型，能帮助你分清工具代码的问题和模型决策的问题。

下面四个项目是上游学习资源，作者和维护者属于各自社区；它们不是 chzFRA 的原创项目。本仓库已实现一个使用官方 MCP SDK 的本地文档检索集成示例；OpenAI Agents SDK、LangGraph 和 Inspect AI 尚未集成。文档工具、适配器与可复现实验才是本仓库逐步积累的贡献。上游资料核对日期：2026-09-30；本地实现状态更新：2026-10-01。实现练习时应记录实际使用的依赖版本。

## 先选一个问题，再读相关代码

| 官方项目 | 主要学什么 | 可以给 AgentTrace Lab 带来什么 |
| --- | --- | --- |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | Agent 循环、工具调用、交接和 tracing | 设计一个把 SDK 工具事件转换为本地轨迹的可选适配器 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 状态、检查点、恢复和人工介入 | 观察恢复前后哪些节点重新执行，以及是否重复产生副作用 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 工具的标准接口、客户端与服务端 | 已有真实 stdio 文档检索示例，可学习错误标记适配与调用回归检查 |
| [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | 数据集、执行过程、评分器和评估日志 | 把“调用行为正确”和“任务回答正确”分开评估 |

核对时四个仓库的核心代码许可证均为 MIT：[Agents SDK](https://github.com/openai/openai-agents-python/blob/main/LICENSE)、[LangGraph](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)、[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE)、[Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/LICENSE)。复用代码时保留对应的许可证和来源；依赖、服务与数据另看各自条款。

### 1. OpenAI Agents SDK：看懂一次工具调用

先读官方 [SDK 入口](https://developers.openai.com/api/docs/guides/agents/sdk)和 [Integrations and observability](https://developers.openai.com/api/docs/guides/agents/integrations-observability)。SDK 在应用进程中执行 Agent 循环，并支持记录模型、工具、交接等事件。

**练习：**写一个只读 `lookup_note` 工具，为成功、无结果、参数错误各准备一个输入。先直接调用 Python 函数，保存本地轨迹；以后接入 SDK 时，对照同一组输入检查工具事件如何映射。

**验收：**每次工具执行都有可关联的调用 ID、结果状态和耗时；模型重试不能被误算成一次调用；输出一份字段对应表，注明哪些 SDK 字段在本地格式中没有对应项。真实模型运行是可选步骤，需要相应提供商配置，不能用离线测试结果冒充真实模型成绩。

**可公开成果：**一篇“工具失败在 trace 中长什么样”的短文，附三个脱敏样例、复现命令和适用版本。若发现上游问题，先提交最小复现 issue；核对时该仓库不接受非协作者直接提交 PR，见其[贡献说明](https://github.com/openai/openai-agents-python#contributing)。

### 2. LangGraph：理解中断和恢复

先读 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。检查点用于保存一次对话线程的图状态，store 用于跨线程的长期数据；这两个概念需要分开理解。

**练习：**使用普通 Python 节点搭建“读资料 → 整理 → 人工确认”的三步流程；不接模型也能练习状态迁移。在确认前中断，再恢复执行。给每个节点加调用计数，刻意让一个节点失败一次。

**验收：**写清哪个节点重跑了、哪个没有；用临时文件作为可观察的副作用，验证恢复逻辑是否导致重复写入。进程重启的实验要使用持久化存储，不能用内存检查点得出跨进程恢复结论。

**可公开成果：**一份带轨迹的失败复现和修复前后比较。先解决自己的重复执行问题，再考虑提交上游测试或文档改进。

### 3. MCP Python SDK：把工具接成真实接口

先读官方 [Python SDK](https://github.com/modelcontextprotocol/python-sdk) 和 [服务端教程](https://modelcontextprotocol.io/docs/develop/build-server)。SDK 提供客户端与服务端能力，用于暴露和调用 tools、resources 等接口。请按所选 SDK 版本使用文档，避免混用不同主版本的示例。

**先运行已实现的案例：**按 [MCP 文档检索示例](../examples/mcp_document_search/README.md)建立独立环境并运行 `verify`。示例使用官方 SDK **2.2.0**，通过真实 stdio 客户端与服务端子进程调用 `search_documents` 和 `read_document`；本地 **28 项验收通过**，无需模型或密钥。再运行示例中的 `search "run_start" --root docs` 命令，检查它在本项目文档中找到的路径、行号与摘录。

**解释现有验收：**读懂工具发现与参数 schema；比较正常检索、空结果、重复读取和缺失文件的轨迹。重点解释为什么 MCP 返回 `isError=true` 时，原始 Python 调用可以不抛异常，以及为什么 [显式适配器](../examples/mcp_document_search/adapter.py)要在被追踪的函数内抛出 `MCPToolError`。重复读取使调用数从 2 增到 3，比较命令应退出 `1`；错误预算为零时，适配后的工具失败也应使检查失败。这些预期失败是验收通过的条件，空搜索结果则保持成功。

**扩展练习：**在明确指定的非敏感目录加入自己的文档，写出固定查询和预期命中；再增加服务端意外退出、传输错误与恢复的验收。这些关闭/传输故障测试尚未实现，应与现有工具错误案例区分。现有示例已检查相对路径越界、绝对路径、符号链接、隐藏文件、文件大小和编码等边界；它是受限的本地例子，不是任意 MCP 服务的通用适配器。

**可公开成果：**基于已有代码和 [验证记录](../examples/mcp_document_search/VERIFICATION.md)，写一篇“为什么 RPC 成功不代表工具成功”的复现说明，再附上自己的扩展实验。遇到上游缺陷时，附 SDK 版本及最小 client/server 代码；不要把本地集成验收描述为真实用户反馈。

### 4. Inspect AI：给改进留下证据

先读 [Tutorial](https://inspect.aisi.org.uk/tutorial.html) 和 [Log Files](https://inspect.aisi.org.uk/eval-logs.html)。Inspect 将任务样本、执行逻辑和评分组合为评估任务，并提供日志读取与查看能力。

**练习：**整理 12 个有明确预期的小案例：4 个正常输入、4 个输入或工具故障、4 个缺少证据的请求。先给每个案例人工标注正确结果，再尝试把运行结果导入评估流程。模型调用可留到最后。

**验收：**分别报告任务成功率、执行错误数、工具调用次数和耗时；保留失败样例。比较前后版本时使用同一批输入与条件。小样本只支持对这批案例的结论；规则判断通过也不等于模型回答质量高。

**可公开成果：**一次能复现的实验，说明一个改动改善了什么、增加了什么成本、哪些问题仍未解决。多模态可作为后续延伸：加入自制或许可明确的页面图片，对页码引用和内容回答分别标注，不在尚未运行模型时宣称视觉理解能力。

## 六周实践节奏

这是建议的学习安排，不是已实现功能清单，也不保证获得关注或 star。

| 周次 | 做一件具体的事 | 留下可检查的成果 |
| --- | --- | --- |
| 第 1 周 | 运行本仓库示例，手动追踪一个成功案例和一个失败案例 | 复现命令、两份轨迹解读、一个你能解释的测试 |
| 第 2 周 | 为自己的只读 Python 工具记录调用，学习 Agents SDK 的事件结构 | 三类输入、字段对应表、日志不包含密钥的检查 |
| 第 3 周 | 运行并解释已有 MCP 案例，再用自己的文档扩展 | `isError` 适配对照、固定查询与预期命中、确定的依赖版本 |
| 第 4 周 | 用 LangGraph 做一次中断恢复实验 | 节点执行计数、重复副作用的回归测试 |
| 第 5 周 | 参考 Inspect 建立固定案例集，完成一次版本比较 | 数据来源、评分标准、完整失败样例和结果表 |
| 第 6 周 | 选择一个真实发现，整理成别人能复现的 issue、文档改进或版本发布 | 复现步骤、代码、测试、限制；按上游贡献政策选择提交形式 |

从仓库根目录开始：

```bash
python3 -m agent_trace_lab demo --output artifacts/demo
python3 -m unittest discover -s tests -v
```

本仓库当前能运行的内容以 [README](../README.md) 为准。真实 MCP 集成的安装和验收入口见 [示例 README](../examples/mcp_document_search/README.md)；OpenAI Agents SDK、LangGraph、Inspect AI 集成及上述 MCP 扩展练习仍属于后续工作。

## 让主页展示真实积累

主页保留本项目入口，以及这份有练习目标的学习路线。每完成一个实验，在项目中增加可运行示例和结果解释；每形成一个稳定改进，再发布一个版本。上游项目放在“正在学习”区域，自己的实现放在“我维护的项目”区域。

每篇项目记录回答四个问题就够了：遇到什么具体问题、如何复现、修改了什么、证据说明了什么。逐步积累能被别人复现、使用和反馈的成果，是比增加仓库数量更可控的目标。
