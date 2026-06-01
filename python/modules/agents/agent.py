"""
LangGraph Agent - 真正的 AI Agent
使用 LangGraph 的 create_react_agent 构建 ReAct Agent，
支持自主决策：思考 → 调用工具 → 观察结果 → 评估思考 → 最终回答。
使用 stream_mode="messages" 实现逐 token 流式输出。

Agent 生命周期事件流：
1. 🧠 thinking      - LLM 的推理过程（决定调用工具前的思考）
2. 🔧 tool_call     - LLM 决定调用工具
3. 👀 observation   - 工具执行结果的结构化摘要（代码生成）
4. 🧠 evaluating    - LLM 对工具结果的评估思考（判断是回答还是继续调用工具）
5. 🔄 (可重复 2-4)  - 多轮 tool calling
6. 💬 text          - 最终回答
"""
import json
import logging
from typing import AsyncGenerator, Optional

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL
from modules.agents.state import AgentState
from modules.agents.tools.retrieval_tool import (
    retrieve_knowledge,
    set_current_knowledge_base_id,
)
from modules.agents.tools.datetime_tool import get_current_datetime
from modules.agents.tools.summarize_tool import summarize_document

logger = logging.getLogger(__name__)

# Agent 系统提示词
SYSTEM_PROMPT = """你是一个智能AI助手，名叫小R。你是个人RAG知识库系统的核心Agent。

你有多个工具可以使用，每个工具都有详细的说明。请根据用户的问题，自主判断应该使用哪个工具（或不使用工具直接回答）。

## 工作方式
在回答用户问题前，请先进行内部推理：
1. 分析用户的需求是什么
2. 判断是否需要使用工具，以及为什么选择某个工具
3. 如果使用了工具，**先输出你对工具返回结果的分析和评估思考过程**，再根据评估结果决定是继续调用工具还是给出最终回答

## 回答风格
- 用中文回答，语气自然友好
- 回答简洁清晰，避免过于冗长
- 如果不知道答案，如实告知，不要编造信息
- 如果使用了检索工具，在回答中引用信息来源

## Markdown 格式要求（必须严格遵守）
你输出的**所有内容**（包括思考过程、评估思考、最终回答）都必须使用规范的 Markdown 格式。以下是必须遵守的规则：

### 标题
- 标题标记（#、##、### 等）**后面必须加一个空格**再写内容
- ✅ 正确：`### 核心主题`
- ❌ 错误：`###核心主题`

### 列表
- 无序列表标记（-、*）**后面必须加一个空格**再写内容
- 有序列表标记（1.、2.）**后面必须加一个空格**再写内容
- ✅ 正确：`- 项目一`、`1. 第一步`
- ❌ 错误：`-项目一`、`1.第一步`

### 加粗和斜体
- 加粗标记 `**文本**` 必须正确闭合，**前后各两个星号**
- 斜体标记 `*文本*` 必须正确闭合，**前后各一个星号**
- 加粗/斜体标记与中文内容之间**不需要加空格**，但必须确保标记完整
- ✅ 正确：`**核心主题**`、`*斜体内容*`
- ❌ 错误：`**核心主题`（缺少闭合）、`核心主题**`（缺少开头）

### 分隔线
- 分隔线 `---` **前后必须各有一个空行**
- ✅ 正确：
  ```
  上文

  ---

  下文
  ```
- ❌ 错误：
  ```
  上文
  ---
  下文
  ```

### 引用
- 引用标记 `>` **后面必须加一个空格**再写内容
- ✅ 正确：`> 引用内容`
- ❌ 错误：`>引用内容`

### 代码
- 行内代码使用单个反引号 `` `代码` ``
- 代码块使用三个反引号 ``` ```代码``` ``` 并指定语言

**重要：请务必检查你的输出，确保所有 Markdown 标记都符合上述规范。不规范的 Markdown 会导致前端渲染失败，显示原始的标记符号。**
## 重要规则
- **工具调用后，你必须先输出对工具结果的评估思考过程，再给出最终回答**
- 评估思考过程应该包括：工具返回了什么信息、这些信息是否足够回答问题、是否需要进一步调用其他工具
- 不要跳过评估思考步骤直接给出回答
- **请使用以下格式输出评估思考和回答**：
  【评估思考】<你对工具结果的分析和评估过程>
  【回答】<你的最终回答>
- **注意：【评估思考】和【回答】标记必须严格使用完整格式，包括中文方括号【】和结尾的】**
- **正确示例**：【评估思考】这是分析过程 【回答】这是最终回答
- **错误示例**：【评估思考 或 【回答（缺少结尾的】）
- 如果没有使用工具，直接输出回答即可，不需要【评估思考】标记
- **重要：不要在【回答】标记后直接跟内容而不加】符号，必须写成【回答】而不是【回答**
"""




class LangGraphAgent:
    """基于 LangGraph 的 ReAct Agent"""

    def __init__(self):
        if not DEEPSEEK_API_KEY:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. "
                "Please configure it in .env file."
            )

        # 初始化 LLM（DeepSeek 支持 tool calling）
        self.llm = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_API_BASE,
            temperature=0.7,
            max_tokens=4096,
            # 启用流式输出
            streaming=True,
        )

        # 定义工具集（现在有 3 个工具）
        self.tools = [retrieve_knowledge, summarize_document, get_current_datetime]

        # 创建 ReAct Agent
        # create_react_agent 会自动处理：
        # 1. LLM 思考 → 决定调用工具还是直接回答
        # 2. 如果调用工具 → 执行工具 → 将结果返回给 LLM
        # 3. LLM 继续思考 → 直到给出最终回答
        self.graph = create_react_agent(
            self.llm,
            self.tools,
            state_schema=AgentState,
            prompt=SYSTEM_PROMPT,
            version="v2",  # 使用 LangGraph v2 版本
        )

        logger.info(
            f"LangGraphAgent initialized with model: {DEEPSEEK_MODEL}, "
            f"tools: {[t.name for t in self.tools]}"
        )

    async def stream(
        self,
        message: str,
        knowledge_base_id: str,
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        流式执行 Agent（逐 token 流式输出）

        Args:
            message: 用户消息
            knowledge_base_id: 知识库 ID（可选）
            conversation_id: 对话 ID（可选）

        Yields:
            事件字典：
            - {"type": "thinking", "content": "LLM 的思考过程"}
            - {"type": "tool_call", "tool": "tool_name", "args": {...}}
            - {"type": "observation", "content": "工具执行结果的结构化摘要"}
            - {"type": "tool_result", "content": "工具执行结果"}
            - {"type": "text", "content": "文本片段（逐 token）"}
            - {"type": "sources", "content": [...]}
            - {"type": "done"}
        """
        # 设置全局 knowledge_base_id（供 retrieval_tool 读取）
        set_current_knowledge_base_id(knowledge_base_id)

        # 构建输入状态
        inputs = {
            "messages": [("user", message)],
            "knowledge_base_id": knowledge_base_id,
            "conversation_id": conversation_id,
            "sources": None,
            "remaining_steps": 25,  # LangGraph v2 必需，最大推理步数
        }

        # 用于累积完整的回答文本
        full_response = ""

        # 阶段追踪：
        # "thinking"   → LLM 在决定调用工具前的推理过程
        # "acting"     → LLM 决定调用工具
        # "evaluating" → 工具执行完毕后，LLM 对工具结果的评估思考
        # "responding" → LLM 生成最终回答
        phase = "thinking"

        # 累积当前阶段的 thinking 内容（用于多轮 tool calling）
        current_thinking = ""

        # 评估思考标记检测
        # LLM 被要求在工具调用后使用 【评估思考】 和 【回答】 标记分隔评估思考和最终回答
        # 使用 marker_buffer 跨 chunk 边界累积检测 【回答】 标记
        # 使用正则表达式匹配多种变体：
        # - 【回答】  (标准格式)
        # - 【回答   (缺少结尾】)
        # - 【回答】  (完整格式)
        import re
        RESPONSE_MARKER_PATTERN = re.compile(r'【回答】?')
        marker_buffer = ""

        # 追踪 tool calling 轮次
        tool_call_round = 0

        # 记录最后一次工具调用的信息（用于生成 observation 摘要）
        last_tool_call_name = ""
        last_tool_call_args = {}

        try:
            # 用于累积 tool_call_chunks 中的 args（因为 args 是增量式流式输出的）
            # 键：tool_call_id，值：{"name": "", "args": ""}
            accumulated_tool_calls = {}

            # 使用 stream_mode="messages" 实现逐 token 流式输出
            async for msg_chunk, metadata in self.graph.astream(
                inputs, stream_mode="messages"
            ):
                node_name = metadata.get("langgraph_node", "")

                if node_name == "agent":
                    # ========== Agent 节点的输出（逐 token 流式） ==========

                    # 1. 检测 tool_calls（工具调用决策）
                    # tool_call_chunks 是增量式流式输出的，每个 chunk 包含部分信息
                    # 我们需要累积所有 chunks，直到收到完整的 tool_call
                    if hasattr(msg_chunk, "tool_call_chunks") and msg_chunk.tool_call_chunks:
                        for tcc in msg_chunk.tool_call_chunks:
                            tc_id = tcc.get("id", "")
                            if not tc_id:
                                continue

                            # 累积 tool_call 信息
                            if tc_id not in accumulated_tool_calls:
                                accumulated_tool_calls[tc_id] = {"name": "", "args": ""}

                            name = tcc.get("name", "")
                            if name:
                                accumulated_tool_calls[tc_id]["name"] = name

                            args_str = tcc.get("args", "")
                            if args_str:
                                accumulated_tool_calls[tc_id]["args"] += args_str

                        # 检查是否有完整的 tool_call（name 不为空且 args 是完整 JSON）
                        for tc_id in list(accumulated_tool_calls.keys()):
                            tc_info = accumulated_tool_calls[tc_id]
                            tool_name = tc_info["name"]
                            args_accumulated = tc_info["args"]

                            if not tool_name:
                                continue

                            # 尝试解析 args，如果解析成功说明 args 已完整
                            try:
                                args = json.loads(args_accumulated) if args_accumulated else {}
                            except json.JSONDecodeError:
                                # args 还不完整，跳过，等待下一个 chunk
                                continue

                            # 解析成功，移除已处理的 tool_call
                            del accumulated_tool_calls[tc_id]

                            # 自动注入 knowledge_base_id
                            if tool_name in ("retrieve_knowledge", "summarize_document"):
                                args["knowledge_base_id"] = knowledge_base_id
                                logger.info(
                                    f"Injected knowledge_base_id into tool call: "
                                    f"{knowledge_base_id}"
                                )

                            tool_call_round += 1
                            logger.info(
                                f"Agent decided to call tool (round {tool_call_round}): "
                                f"{tool_name} with args: {args}"
                            )

                            # 先发送累积的 thinking 内容（如果有）
                            # 这包含了 LLM 对工具结果的评估思考
                            if current_thinking.strip():
                                yield {
                                    "type": "thinking",
                                    "content": current_thinking.strip(),
                                }
                                current_thinking = ""

                            # 记录工具调用信息
                            last_tool_call_name = tool_name
                            last_tool_call_args = args

                            yield {
                                "type": "tool_call",
                                "tool": tool_name,
                                "args": args,
                            }

                        # 切换到 acting 阶段
                        phase = "acting"

                    # 2. 检测文本内容（逐 token 流式输出）
                    if hasattr(msg_chunk, "content") and msg_chunk.content:
                        content = msg_chunk.content
                        if not content.strip():
                            continue

                        if phase == "thinking":
                            # 累积 LLM 的思考过程
                            current_thinking += content

                        elif phase == "evaluating":

                            # LLM 对工具结果的评估思考
                            # 使用 marker_buffer 跨 chunk 边界累积检测 【回答】 标记
                            # 使用正则表达式匹配多种变体：
                            #   【回答】  (标准格式，带】)
                            #   【回答   (缺少结尾】)
                            # 检测到标记 → 标记前内容作为 evaluation，标记后内容作为 text，切换到 responding
                            # 未检测到标记 → 只累积到 marker_buffer，不发送（避免后续检测到标记时重复发送）
                            marker_buffer += content

                            # 使用正则搜索匹配的标记
                            marker_match = RESPONSE_MARKER_PATTERN.search(marker_buffer)
                            if marker_match:
                                # 在缓冲区中检测到 【回答】 标记（含变体）
                                marker_start = marker_match.start()
                                marker_end = marker_match.end()
                                eval_part = marker_buffer[:marker_start].strip()
                                response_part = marker_buffer[marker_end:].strip()

                                # 去掉 eval_part 中的 【评估思考】 标记（如果存在）
                                eval_part = eval_part.replace("【评估思考】", "").strip()

                                # 发送完整的评估思考内容（一次性发送，避免重复）
                                if eval_part:
                                    current_thinking += eval_part
                                    yield {"type": "evaluation", "content": eval_part}

                                # 切换到 responding 阶段
                                phase = "responding"
                                marker_buffer = ""

                                # 发送标记后的回答内容
                                if response_part:
                                    full_response += response_part
                                    yield {"type": "text", "content": response_part}
                            else:
                                # 还没有检测到标记，只累积到 marker_buffer，不 yield 发送
                                # 等检测到 【回答】 标记时再从 marker_buffer 中提取完整内容一次性发送
                                # 这样可以避免同一内容在 else 分支和 if 分支中被重复发送
                                pass


                        elif phase == "responding":
                            # LLM 生成最终回答
                            # 但也要检测是否 LLM 又开始输出 【评估思考】（多轮 tool calling 场景）
                            # 如果检测到 【评估思考】 标记，切换回 evaluating 阶段
                            if "【评估思考】" in content:
                                # 标记前的内容作为 text 发送
                                parts = content.split("【评估思考】", 1)
                                before_marker = parts[0].strip()
                                after_marker = parts[1].strip()
                                if before_marker:
                                    full_response += before_marker
                                    yield {"type": "text", "content": before_marker}
                                # 切换到 evaluating 阶段
                                # 将标记后的内容放入 marker_buffer，由 evaluating 阶段的逻辑统一处理
                                # 不要直接 yield 发送，避免与后续 evaluating 阶段的处理重复
                                phase = "evaluating"
                                marker_buffer = ""
                                if after_marker:
                                    # 去掉可能的 【回答】 标记（使用正则匹配多种变体）
                                    after_marker = RESPONSE_MARKER_PATTERN.sub("", after_marker).strip()
                                    if after_marker:
                                        marker_buffer = after_marker

                            else:
                                # 处理流式 chunk 边界问题：
                                # 当 evaluating 阶段的正则匹配到不完整的 【回答（缺少】）时，
                                # 后续 chunk 中的 】符号会被当作回答内容发送。
                                # 这里检测并去掉回答内容开头的多余 】符号。
                                if content.startswith("】"):
                                    content = content[1:]
                                if content:
                                    full_response += content
                                    yield {"type": "text", "content": content}

                elif node_name == "tools":
                    # ========== 工具节点的输出 ==========
                    if hasattr(msg_chunk, "content") and msg_chunk.content:
                        content = msg_chunk.content

                        # 检查是否包含 sources 信息
                        if "__SOURCES__:" in content:
                            # 提取 sources 部分
                            parts = content.split("__SOURCES__:")
                            if len(parts) > 1:
                                sources_str = parts[1].strip()
                                try:
                                    sources_data = json.loads(sources_str)
                                    yield {
                                        "type": "sources",
                                        "content": sources_data,
                                    }
                                    logger.info(
                                        f"Sent {len(sources_data)} sources to frontend"
                                    )
                                except json.JSONDecodeError as e:
                                    logger.warning(
                                        f"Failed to parse sources JSON: {e}"
                                    )

                            # 提取工具执行结果（不含 sources 标记）
                            clean_content = parts[0].strip()
                            if clean_content:
                                # 发送 tool_result（完整结果）
                                yield {
                                    "type": "tool_result",
                                    "content": clean_content,
                                }
                        else:
                            yield {
                                "type": "tool_result",
                                "content": content,
                            }

                    # 工具执行完毕后，生成 observation 摘要并切换到 evaluating 阶段
                    # observation 是工具结果的结构化摘要（由代码生成）
                    observation = _make_observation_summary(
                        last_tool_call_name,
                        last_tool_call_args,
                        content if 'content' in dir() else "",
                    )
                    yield {
                        "type": "observation",
                        "content": observation,
                    }

                    # 切换到 evaluating 阶段
                    # LLM 将基于工具结果进行评估思考
                    phase = "evaluating"

            # 处理流结束后剩余的 evaluation 内容
            # 如果 phase 仍然是 evaluating，说明 LLM 没有输出 【回答】 标记
            # 此时将 marker_buffer 中的内容作为 evaluation 事件发送
            # （因为 evaluating 阶段的 else 分支不再 yield 发送，内容都累积在 marker_buffer 中）
            if phase == "evaluating":
                eval_content = marker_buffer.strip()
                if eval_content:
                    # 去掉 【评估思考】 标记（如果存在）
                    eval_content = eval_content.replace("【评估思考】", "").strip()
                    yield {"type": "evaluation", "content": eval_content}
                    # 如果没有 full_response，将评估思考也作为回答
                    if not full_response:
                        full_response = eval_content
                        yield {"type": "text", "content": eval_content}


            # 发送剩余的 thinking 内容（如果有）
            if current_thinking.strip() and phase != "evaluating":
                yield {
                    "type": "thinking",
                    "content": current_thinking.strip(),
                }

            # 发送完成事件
            yield {"type": "done", "content": full_response}

        except Exception as e:
            logger.error(f"Agent stream error: {e}", exc_info=True)
            yield {
                "type": "error",
                "content": f"生成回答时出错: {str(e)}",
            }


def _make_observation_summary(tool_name: str, tool_args: dict, tool_result: str) -> str:
    """
    生成工具执行结果的结构化观察摘要。
    由代码自动生成，展示工具返回了什么信息。
    """
    # 获取工具的中文名称
    tool_display_name = {
        "retrieve_knowledge": "检索知识库",
        "summarize_document": "总结文档",
        "get_current_datetime": "获取当前时间",
    }.get(tool_name, tool_name)

    # 获取工具参数摘要
    args_summary = ""
    if tool_name == "retrieve_knowledge":
        query = tool_args.get("query", "")
        args_summary = f'查询: "{query}"'
    elif tool_name == "summarize_document":
        filename = tool_args.get("filename", "")
        args_summary = f'文档: "{filename}"'

    # 获取结果长度/数量摘要
    result_summary = ""
    if tool_result:
        # 尝试解析 JSON（检索结果通常是 JSON 数组）
        try:
            data = json.loads(tool_result)
            if isinstance(data, list):
                result_summary = f"返回了 {len(data)} 条结果"
            elif isinstance(data, dict):
                keys = list(data.keys())
                result_summary = f"返回了包含 {len(keys)} 个字段的数据"
        except (json.JSONDecodeError, TypeError):
            # 普通文本，统计长度
            char_count = len(tool_result)
            if char_count > 100:
                result_summary = f"返回了 {char_count} 字符的内容"
            else:
                result_summary = f"返回内容: {tool_result[:100]}"

    # 组合摘要
    parts = [f"🔧 工具: {tool_display_name}"]
    if args_summary:
        parts.append(args_summary)
    if result_summary:
        parts.append(result_summary)

    return " | ".join(parts)


# 全局单例
_agent: Optional[LangGraphAgent] = None


def get_agent() -> LangGraphAgent:
    """获取 LangGraph Agent 单例"""
    global _agent
    if _agent is None:
        _agent = LangGraphAgent()
    return _agent
