"""
Agent State - LangGraph 状态定义
"""
from typing import Annotated, Sequence, TypedDict, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    Agent 状态
    
    messages: 消息历史（自动追加）
    knowledge_base_id: 当前知识库 ID
    conversation_id: 当前对话 ID
    sources: 检索来源信息（用于前端展示）
    remaining_steps: 剩余推理步数（LangGraph v2 必需）
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    knowledge_base_id: Optional[str]
    conversation_id: Optional[str]
    sources: Optional[str]  # JSON string of sources for frontend
    remaining_steps: int  # LangGraph v2 必需字段，控制最大推理步数
