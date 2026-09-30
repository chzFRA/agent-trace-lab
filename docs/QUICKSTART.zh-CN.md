# 从一个报错开始使用 AgentTrace Lab

适合正在写 Python 工具或 Agent 的开发者。你需要能修改这些工具的代码。
本项目帮你记录工具执行、定位异常，并在改动后检查调用行为。它不会自动监控
ChatGPT 网页、替你规划任务、修复代码或判断模型答案是否正确。

## 先体验：不安装依赖、不申请模型密钥

需要 Python 3.9 或更新版本。在终端运行：

```bash
git clone https://github.com/chzFRA/agent-trace-lab.git
cd agent-trace-lab
python3 -m agent_trace_lab demo --output artifacts/demo
```

Windows 上可以把 `python3` 换成 `python`。如果你已经下载了仓库，直接进入它的
文件夹，从最后一行开始。用浏览器打开以下本地文件（macOS 可用 `open 文件路径`）：

| 文件 | 你应该看到什么 |
| --- | --- |
| `artifacts/demo/candidate/report.html` | 正常执行：4 次工具调用、0 次错误 |
| `artifacts/demo/failure/report.html` | 读取不存在的文件失败：1 次工具错误；错误预算检查失败 |
| `artifacts/demo/comparison/report.html` | 去掉一次多余读取后，总调用数从 5 变 4，并列出具体工具的变化 |

在报告里用工具名或状态筛选，展开一条调用，可以看到调用它的上层工具、事件编号
和错误类型。默认没有记录参数和文档内容。重复出现的同名函数不一定代表重复工作，
需要结合业务逻辑确认。

`demo` 会替换输出目录中的示例文件和报告。请给它单独的输出目录。

## 接到你自己的 Python 函数

在你的应用虚拟环境里安装本项目（下面的路径换成你实际下载的位置）：

```bash
python -m pip install /path/to/agent-trace-lab
```

这是一个完整示例。存为 `my_first_trace.py`，在有 `README.md` 的目录运行它：

```python
from pathlib import Path
from agent_trace_lab import TraceSession

def read_document(path):
    return Path(path).read_text(encoding="utf-8")

with TraceSession("my-run.jsonl", name="read-my-document") as trace:
    # 包装已有函数；Agent 需要实际调用这个包装后的函数。
    tracked_read = trace.tool("read_document")(read_document)
    text = tracked_read("README.md")
    print("Read", len(text), "characters")

if trace.logging_errors:
    raise RuntimeError("工具执行结束，但记录不完整：" + repr(trace.logging_errors))
```

然后运行：

```bash
python my_first_trace.py
agent-trace inspect my-run.jsonl --max-errors 0 --max-cancelled 0 --output my-report
```

打开 `my-report/report.html`。再次运行时，请换一个记录文件名；默认不会覆盖已有
记录。只有明确要替换它时才使用 `TraceSession(..., overwrite=True)`。

重要：这里只检查你包装的函数；函数里没有包装的内部步骤不会自动变成独立记录。
普通函数和 `async def` 都支持，所有任务必须在 `with` 结束前等待完成。

## 改完代码以后检查有没有退步

用相同任务、相同输入分别得到 `before.jsonl` 和 `after.jsonl`：
把上面示例里的记录文件名先改成 `before.jsonl` 运行；修改你的函数后，改成
`after.jsonl` 再运行。两个文件都保留，然后执行：

```bash
agent-trace compare before.jsonl after.jsonl --per-tool --output comparison
```

默认不允许总调用数、错误数或取消次数增加。`--per-tool` 还会逐个工具检查，避免
“A 少一个错误，B 多一个错误，总数没变”这种情况被掩盖。报告会直接列出 A、B 的变化。
调用减少并不能证明答案仍然正确，你还需要单独验证任务结果。

命令退出码 `0` 表示检查通过，`1` 表示发现问题，`2` 表示输入或文件操作有误。
在 CI 中，退出码 `1` 会让步骤失败，不是自动撤销代码。

## 真实 MCP 工具怎么接

[MCP 文档搜索示例](../examples/mcp_document_search/README.md)会真正启动一个本地
MCP 服务端，用客户端搜索和读取文档，并验证错误记录。

这里有一个实际发现：MCP 的工具错误可能通过返回值 `isError=true` 表示，Python
函数本身并没有抛异常。只加普通装饰器会把这种请求记为正常返回。示例提供的适配层
会将这个结果转成明确的 `MCPToolError`，让错误预算检查捕获它。这是适配层刻意定义的
行为；成功结果保持原样。示例需要单独安装官方 SDK，不影响标准库核心。

## 使用范围

- 一次记录最多 20 MiB。超过限制停止记录并警告，原工具继续运行。检查
  `trace.logging_errors`；不要把部分记录当成完整成功。建议每个任务开一个 session。
- 日志也有额外耗时和磁盘占用。不要把微小循环中的每个运算都当作工具来记录。
- 默认不保存输入、输出和错误消息；工具名、异常类型和耗时仍会记录。
- 尚未覆盖所有框架、跨进程调用关系或流式生成器。SDK 适配和现场测量范围见
  [验证记录](VALIDATION.md)。
