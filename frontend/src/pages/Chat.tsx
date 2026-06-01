import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

/**
 * 预处理 Markdown 文本，修复 LLM 生成的不规范格式
 * 
 * LLM 经常生成不规范的 Markdown，例如：
 * - "###核心主题"（标题后无空格）
 * - "-项目"（列表标记后无空格）
 * - "---" 前后无空行
 * - "###1.报错原因"（标题标记后跟数字列表）
 * 
 * 注意：**不加**空格在 ** 与中文之间，因为 Markdown 规范允许 **中文** 这样的用法，
 * 加空格反而会破坏有效的 Markdown 语法。
 * ReactMarkdown + remarkGfm 完全支持中文加粗语法如 **核心主题**。
 */
function preprocessMarkdown(text: string): string {
  if (!text) return text;

  let result = text;

  // ===== 1. 修复标题标记后缺少空格的问题 =====
  //    "###核心主题" → "### 核心主题"
  //    也处理 "###1.报错原因" → "### 1.报错原因"（标题后跟数字列表）
  //    注意：必须匹配完整的 # 序列（1-6个），且后面紧跟非空格、非#的字符
  //    使用 \b 单词边界确保 # 序列是完整的（后面没有更多 #）
  //    或者使用负向先行断言确保后面不是 #
  result = result.replace(/(^|\n)(#{1,6})(?!\s)(?!#)/gm, '$1$2 ');

  // ===== 2. 修复列表标记后缺少空格的问题 =====
  //    行首无序列表：-/*/+ 后跟非空白、非-、非*的字符（避免匹配 --- 或 **）
  result = result.replace(/^(\s*[-*+])(?![\s\-*])/gm, '$1 ');
  result = result.replace(/^(\s*\d+\.)(?=\S)/gm, '$1 ');
  //    行内列表（不在行首）："服务未启动-配置冲突" → "服务未启动\n- 配置冲突"
  //    匹配：中文/句号/问号等后跟 - 再跟中文（中间无空格）
  result = result.replace(/([\u4e00-\u9fff\u3002\uff1f\uff01\u3001])\s*-([\u4e00-\u9fff])/g, '$1\n- $2');

  // ===== 3. 修复分隔线 --- 前后换行问题 =====
  //    如果 --- 前后没有空行，添加空行
  result = result.replace(/([^\n])\n---\n([^\n])/g, '$1\n\n---\n\n$2');

  // ===== 4. 修复引用标记后缺少空格的问题 =====
  //    ">引用" → "> 引用"
  result = result.replace(/^>([^>\s])/gm, '> $1');

  // ===== 5. 修复连续标题之间缺少换行和空格的问题 =====
  //    "关键要点###1.报错原因" → "关键要点\n\n### 1.报错原因"
  //    匹配：非换行、非#字符后跟完整的 # 序列（1-6个），后面跟数字
  //    使用 [^\n#] 避免把 ## 拆成 #\n\n#
  //    注意：步骤1已经处理了行首的 ###后跟非空格，所以这里只处理行中的情况
  result = result.replace(/([^\n#])(#{1,6})(\d)/g, '$1\n\n$2 $3');

  return result;
}


import {
  listKnowledgeBases,
  sendChatMessage,
  listConversations,
  getConversation,
  deleteConversation,
  KnowledgeBase,
  ChatMessage,
  SSEChatEvent,
  Conversation,
} from '../api/index';

// 扩展 ChatMessage 类型，增加 thinking、evaluation 和 observations 字段
interface ExtendedChatMessage extends ChatMessage {
  thinking?: string;
  evaluation?: string;  // LLM 对工具结果的评估思考
  observations?: string;  // LLM 对工具结果的观察思考
  toolCalls?: { tool: string; args: any }[];  // 工具调用记录
}


function Chat() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>('');
  const [messages, setMessages] = useState<ExtendedChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [currentToolName, setCurrentToolName] = useState<string | null>(null);
  // 当前 assistant 消息的 thinking 内容（流式累积中）
  const [thinkingContent, setThinkingContent] = useState<string>('');
  // 当前 assistant 消息的 evaluation 内容（流式累积中）- 评估思考独立于初始思考
  const [evaluationContent, setEvaluationContent] = useState<string>('');
  // 当前 assistant 消息的 observation 内容（流式累积中）
  const [observationContent, setObservationContent] = useState<string>('');
  // Agent 工作流阶段
  const [agentPhase, setAgentPhase] = useState<'idle' | 'thinking' | 'tool_calling' | 'observing' | 'evaluating' | 'responding'>('idle');
  // 知识库下拉菜单是否显示
  const [showKbDropdown, setShowKbDropdown] = useState(false);
  // 下拉菜单的 ref，用于点击外部关闭
  const kbDropdownRef = useRef<HTMLDivElement>(null);

  // 对话列表相关
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentAssistantMsgRef = useRef<ExtendedChatMessage | null>(null);
  // 使用 ref 存储 thinking 内容，避免闭包陈旧值问题
  const thinkingContentRef = useRef<string>('');
  // 使用 ref 存储 evaluation 内容
  const evaluationContentRef = useRef<string>('');
  // 使用 ref 存储 observation 内容
  const observationContentRef = useRef<string>('');
  // 使用 ref 存储 tool calls 记录
  const toolCallsRef = useRef<{ tool: string; args: any }[]>([]);

  // 点击外部关闭知识库下拉菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (kbDropdownRef.current && !kbDropdownRef.current.contains(event.target as Node)) {
        setShowKbDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // 加载知识库列表
  useEffect(() => {
    listKnowledgeBases()
      .then((data) => {
        setKnowledgeBases(data);
      })
      .catch((err) => console.error('Failed to load knowledge bases:', err));
  }, []);

  // 加载对话列表
  const loadConversations = useCallback(() => {
    listConversations()
      .then((data) => setConversations(data.conversations))
      .catch((err) => console.error('Failed to load conversations:', err));
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // 自动滚动到底部
  const scrollToBottom = useCallback(() => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 50);
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, thinkingContent, observationContent, scrollToBottom]);

  // 处理 SSE 事件
  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
    switch (event.type) {
      case 'agent':
        // 显示 Agent 调用的工具名
        setCurrentToolName(event.content || null);
        break;

      case 'conversation_id':
        setConversationId(event.conversationId || event.content || null);
        break;

      case 'thinking':
        // LLM 的初始思考过程（tool_call 之前）
        // 存入 thinkingContent，与评估思考（evaluation）分开
        if (event.content) {
          setThinkingContent((prev) => prev + event.content);
          thinkingContentRef.current += event.content;
          setAgentPhase('thinking');
        }
        break;

      case 'evaluation':
        // LLM 对工具结果的评估思考（observation 之后，独立于初始思考）
        // 存入 evaluationContent，与初始思考（thinking）分开
        if (event.content) {
          setEvaluationContent((prev) => prev + event.content);
          evaluationContentRef.current += event.content;
          setAgentPhase('evaluating');
        }
        break;

      case 'tool_call':
        // 记录工具调用
        // 后端发送的 tool_call 事件中，content 是工具名，args 是参数
        if (event.content) {
          toolCallsRef.current.push({
            tool: event.content,
            args: event.args || {},
          });
        }
        setAgentPhase('tool_calling');
        break;

      case 'observation':
        // Agent 观察工具执行结果（LLM 对工具结果的真实思考，逐 token 流式）
        if (event.content) {
          setObservationContent((prev) => prev + event.content);
          observationContentRef.current += event.content;
          setAgentPhase('observing');
        }
        // observation 事件结束后，LLM 将进入评估思考阶段
        // 立即将阶段设为 evaluating，确保阶段指示器正确显示"评估"步骤
        // 即使 LLM 的评估思考 thinking 事件尚未到达，UI 也能提前展示 evaluating 状态
        break;


      case 'text':
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.id === currentAssistantMsgRef.current?.id) {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...lastMsg,
              content: lastMsg.content + event.content,
            };
            return updated;
          }
          return prev;
        });
        // 阶段切换逻辑：
        // - evaluating: 如果已经有 evaluation 内容，切换到 responding（评估思考结束，开始回答）
        //               如果没有 evaluation 内容，保持 evaluating（等待第一个 evaluation 事件）
        // - observing:  observation 刚结束，LLM 直接生成回答（无评估思考），切换到 responding
        // - 其他: 设为 responding
        setAgentPhase((prev) => {
          if (prev === 'evaluating') {
            // 如果已经有 evaluation 内容，说明评估思考阶段已结束，切换到 responding
            // 如果没有 evaluation 内容，保持 evaluating（等待 evaluation 事件先到达）
            if (evaluationContentRef.current) {
              return 'responding';
            }
            return 'evaluating';
          }
          if (prev === 'observing') {
            return 'responding';
          }
          return 'responding';
        });
        break;

      case 'sources':
        if (event.content) {
          try {
            const sources = JSON.parse(event.content);
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1];
              if (lastMsg && lastMsg.role === 'assistant') {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  ...lastMsg,
                  sources: Array.isArray(sources) ? sources : lastMsg.sources,
                };
                return updated;
              }
              return prev;
            });
          } catch {
            // ignore parse error
          }
        }
        break;

      case 'done': {
        // 将 thinking、evaluation、observations、toolCalls 保存到当前 assistant 消息中
        const finalThinking = thinkingContentRef.current;
        const finalEvaluation = evaluationContentRef.current;
        const finalObservations = observationContentRef.current;
        const finalToolCalls = toolCallsRef.current;
        const assistantId = currentAssistantMsgRef.current?.id;
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.id === assistantId) {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...lastMsg,
              thinking: finalThinking || undefined,
              evaluation: finalEvaluation || undefined,
              observations: finalObservations || undefined,
              toolCalls: finalToolCalls.length > 0 ? finalToolCalls : undefined,
            };
            return updated;
          }
          return prev;
        });
        setIsLoading(false);
        setCurrentToolName(null);
        setThinkingContent('');
        setEvaluationContent('');
        setObservationContent('');
        setAgentPhase('idle');
        thinkingContentRef.current = '';
        evaluationContentRef.current = '';
        observationContentRef.current = '';
        toolCallsRef.current = [];
        currentAssistantMsgRef.current = null;
        // 刷新对话列表
        loadConversations();
        break;
      }

      case 'error': {
        const errorThinking = thinkingContentRef.current;
        const errorEvaluation = evaluationContentRef.current;
        const errorObservations = observationContentRef.current;
        const errorToolCalls = toolCallsRef.current;
        const errorAssistantId = currentAssistantMsgRef.current?.id;
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.id === errorAssistantId) {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...lastMsg,
              thinking: errorThinking || undefined,
              evaluation: errorEvaluation || undefined,
              observations: errorObservations || undefined,
              toolCalls: errorToolCalls.length > 0 ? errorToolCalls : undefined,
            };
            return updated;
          }
          return prev;
        });

        setIsLoading(false);
        setCurrentToolName(null);
        setThinkingContent('');
        setEvaluationContent('');
        setObservationContent('');
        setAgentPhase('idle');
        thinkingContentRef.current = '';
        evaluationContentRef.current = '';
        observationContentRef.current = '';
        toolCallsRef.current = [];
        setMessages((prev) => [
          ...prev,
          {
            id: `error-${Date.now()}`,
            role: 'assistant',
            content: event.content || '发生未知错误',
            timestamp: Date.now(),
          },
        ]);
        currentAssistantMsgRef.current = null;
        break;
      }
    }
  }, [loadConversations]);


  // 发送消息
  const handleSend = useCallback(() => {
    const trimmed = inputValue.trim();
    if (!trimmed || isLoading) return;

    // 如果没有选择知识库，提示用户
    if (!selectedKbId) {
      const userMsg: ExtendedChatMessage = {
        id: `user-${Date.now()}`,
        role: 'user',
        content: trimmed,
        timestamp: Date.now(),
      };
      const hintMsg: ExtendedChatMessage = {
        id: `hint-${Date.now()}`,
        role: 'assistant',
        content: '⚠️ 请先选择知识库',
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, userMsg, hintMsg]);
      setInputValue('');
      return;
    }

    const userMsg: ExtendedChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
    };

    const assistantMsg: ExtendedChatMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    };

    currentAssistantMsgRef.current = assistantMsg;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInputValue('');
    setIsLoading(true);
    setCurrentToolName(null);
    setThinkingContent('');
    setEvaluationContent('');
    setObservationContent('');
    setAgentPhase('thinking');
    thinkingContentRef.current = '';
    evaluationContentRef.current = '';
    observationContentRef.current = '';
    toolCallsRef.current = [];

    abortControllerRef.current = sendChatMessage(
      trimmed,
      handleSSEEvent,
      selectedKbId,
      conversationId || undefined
    );
  }, [inputValue, isLoading, selectedKbId, conversationId, handleSSEEvent]);

  // 取消请求
  const handleStop = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsLoading(false);
    setCurrentToolName(null);
    setThinkingContent('');
    setEvaluationContent('');
    setObservationContent('');
    setAgentPhase('idle');
    thinkingContentRef.current = '';
    evaluationContentRef.current = '';
    observationContentRef.current = '';
    currentAssistantMsgRef.current = null;
  }, []);

  // 新对话
  const handleNewChat = useCallback(() => {
    handleStop();
    setMessages([]);
    setConversationId(null);
    setSelectedKbId('');
    setCurrentToolName(null);
    setThinkingContent('');
    setEvaluationContent('');
    setObservationContent('');
    loadConversations();
  }, [handleStop, loadConversations]);

  // 选择历史对话
  const handleSelectConversation = useCallback(async (convId: string) => {
    if (isLoading) return;
    handleStop();

    try {
      const convData = await getConversation(convId);
      if (convData) {
        // 将数据库中的消息转换为 ChatMessage 格式
        const loadedMessages: ExtendedChatMessage[] = convData.messages.map((msg: any) => ({
          id: msg.id || `msg-${Date.now()}-${Math.random()}`,
          role: msg.role as 'user' | 'assistant',
          content: msg.content,
          agentType: msg.agent_type as 'rag' | 'chat' | undefined,
          sources: msg.sources,
          timestamp: msg.timestamp ? new Date(msg.timestamp).getTime() : Date.now(),
        }));
        setMessages(loadedMessages);
        setConversationId(convId);
      }
    } catch (err) {
      console.error('Failed to load conversation:', err);
    }
  }, [isLoading, handleStop]);

  // 删除对话
  const handleDeleteConversation = useCallback(async (e: React.MouseEvent, convId: string) => {
    e.stopPropagation();
    try {
      await deleteConversation(convId);
      // 如果删除的是当前对话，清空消息
      if (convId === conversationId) {
        setMessages([]);
        setConversationId(null);
      }
      loadConversations();
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  }, [conversationId, loadConversations]);

  // 键盘事件
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend]
  );

  // 格式化时间
  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } else if (diffDays === 1) {
      return '昨天';
    } else if (diffDays < 7) {
      return `${diffDays}天前`;
    } else {
      return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
    }
  };

  // 判断消息是否是当前正在接收的 assistant 消息
  const isCurrentAssistant = (msg: ExtendedChatMessage) => {
    return msg.role === 'assistant' && msg.id === currentAssistantMsgRef.current?.id;
  };

  // 获取消息的 thinking 内容（流式中的或已完成的）
  const getThinkingContent = (msg: ExtendedChatMessage): string | null => {
    if (isCurrentAssistant(msg)) {
      return thinkingContent || null;
    }
    return msg.thinking || null;
  };

  // 获取消息的 evaluation 内容（评估思考，独立于初始思考）
  const getEvaluationContent = (msg: ExtendedChatMessage): string | null => {
    if (isCurrentAssistant(msg)) {
      return evaluationContent || null;
    }
    return msg.evaluation || null;
  };

  // 获取消息的 observation 内容
  const getObservationContent = (msg: ExtendedChatMessage): string | null => {
    if (isCurrentAssistant(msg)) {
      return observationContent || null;
    }
    return msg.observations || null;
  };


  // 获取消息的 tool calls
  const getToolCalls = (msg: ExtendedChatMessage): { tool: string; args: any }[] | null => {
    if (isCurrentAssistant(msg)) {
      return toolCallsRef.current.length > 0 ? toolCallsRef.current : null;
    }
    return msg.toolCalls || null;
  };

  // Agent 阶段对应的图标和文字
  const getAgentPhaseInfo = (phase: string) => {
    switch (phase) {
      case 'thinking': return { icon: '🧠', text: '思考中...', color: 'text-purple-600 bg-purple-50 border-purple-200' };
      case 'tool_calling': return { icon: '🔧', text: '调用工具', color: 'text-orange-600 bg-orange-50 border-orange-200' };
      case 'observing': return { icon: '👀', text: '观察结果', color: 'text-blue-600 bg-blue-50 border-blue-200' };
      case 'evaluating': return { icon: '🧠', text: '评估中...', color: 'text-indigo-600 bg-indigo-50 border-indigo-200' };
      case 'responding': return { icon: '💬', text: '生成回答', color: 'text-green-600 bg-green-50 border-green-200' };
      default: return { icon: '', text: '', color: '' };
    }
  };

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-0">
      {/* 左侧对话列表侧边栏 */}
      <div
        className={`${
          showSidebar ? 'w-72' : 'w-0'
        } flex-shrink-0 transition-all duration-300 overflow-hidden border-r bg-white rounded-l-lg`}
      >
        <div className="flex flex-col h-full">
          {/* 侧边栏头部 */}
          <div className="p-3 border-b">
            <button
              onClick={handleNewChat}
              disabled={isLoading}
              className="w-full py-2 px-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition text-sm font-medium disabled:opacity-50"
            >
              + 新对话
            </button>
          </div>

          {/* 对话列表 */}
          <div className="flex-1 overflow-y-auto">
            {conversations.length === 0 ? (
              <div className="p-4 text-center text-gray-400 text-sm">
                暂无历史对话
              </div>
            ) : (
              <div className="py-1">
                {conversations.map((conv) => (
                  <div
                    key={conv.id}
                    onClick={() => handleSelectConversation(conv.id)}
                    className={`group flex items-center px-3 py-2.5 cursor-pointer transition-colors ${
                      conv.id === conversationId
                        ? 'bg-blue-50 border-l-2 border-blue-600'
                        : 'hover:bg-gray-50 border-l-2 border-transparent'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-gray-800 truncate">
                        {conv.title}
                      </div>
                      <div className="text-xs text-gray-400 mt-0.5">
                        {conv.message_count} 条消息 · {formatTime(conv.updated_at)}
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDeleteConversation(e, conv.id)}
                      className="ml-2 p-1 text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                      title="删除对话"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 右侧聊天区域 */}
      <div className="flex-1 flex flex-col">
        {/* 顶部工具栏 */}
        <div className="flex items-center justify-between mb-4 flex-shrink-0">
          <div className="flex items-center gap-3">
            {/* 侧边栏切换按钮 */}
            <button
              onClick={() => setShowSidebar(!showSidebar)}
              className="p-1.5 rounded-lg hover:bg-gray-100 transition text-gray-500"
              title={showSidebar ? '隐藏对话列表' : '显示对话列表'}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {showSidebar ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                )}
              </svg>
            </button>
            <h1 className="text-2xl font-bold">聊天</h1>
            {currentToolName && (
              <span
                className="inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium animate-pulse bg-orange-100 text-orange-800"
              >
                {currentToolName}
              </span>
            )}
          </div>
          <div className="relative" ref={kbDropdownRef}>
            <button
              onClick={() => setShowKbDropdown(!showKbDropdown)}
              disabled={isLoading}
              className="flex items-center gap-2 px-3 py-2 border-2 border-blue-400 bg-blue-50 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-100 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            >
              <span>📚</span>
              <span>{selectedKbId ? knowledgeBases.find(kb => kb.id === selectedKbId)?.name || '请选择知识库' : '请选择知识库'}</span>
              <svg
                className={`w-4 h-4 text-blue-500 transition-transform duration-200 ${showKbDropdown ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {showKbDropdown && (
              <div className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-50 overflow-hidden">
                {knowledgeBases.length === 0 ? (
                  <div className="px-3 py-3 text-sm text-gray-400 text-center">
                    暂无知识库
                  </div>
                ) : (
                  knowledgeBases.map((kb) => (
                    <div
                      key={kb.id}
                      onClick={() => {
                        setSelectedKbId(kb.id);
                        setShowKbDropdown(false);
                      }}
                      className={`flex items-center justify-between px-3 py-2.5 cursor-pointer text-sm transition-colors ${
                        kb.id === selectedKbId
                          ? 'bg-blue-50 text-blue-700 font-medium'
                          : 'text-gray-700 hover:bg-gray-50'
                      }`}
                    >
                      <span>{kb.name}</span>
                      {kb.id === selectedKbId && (
                        <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* 消息列表 */}
        <div className="flex-1 overflow-y-auto border rounded-lg bg-white p-4 mb-4">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-400">
              <p className="text-lg mb-2">开始一段新对话</p>
              <p className="text-sm">选择知识库后发送消息，或直接与 AI 聊天</p>
            </div>
          ) : (
            <div className="space-y-4">
              {messages.map((msg) => {
                const displayThinking = getThinkingContent(msg);
                const displayEvaluation = getEvaluationContent(msg);
                const displayObservations = getObservationContent(msg);
                const displayToolCalls = getToolCalls(msg);
                return (
                  <div
                    key={msg.id}
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div
                      className={`max-w-[80%] rounded-lg px-4 py-2 ${
                        msg.role === 'user'
                          ? 'bg-blue-600 text-white'
                          : 'bg-gray-100 text-gray-900'
                      }`}
                    >
                      {/* ===== Agent 工作流指示器（仅 assistant 消息且正在接收时显示） ===== */}
                      {msg.role === 'assistant' && isCurrentAssistant(msg) && agentPhase !== 'idle' && (
                        <div className="mb-3">
                          <div className="flex items-center gap-2 text-xs">
                            {/* 阶段 1: 思考 */}
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${
                              agentPhase === 'thinking' ? 'bg-purple-50 border-purple-200 text-purple-700 font-medium' : 'text-gray-400'
                            }`}>
                              <span>🧠</span>
                              <span>思考</span>
                            </div>
                            {/* 连接线 */}
                            <div className={`w-4 h-px ${agentPhase === 'tool_calling' || agentPhase === 'observing' || agentPhase === 'evaluating' || agentPhase === 'responding' ? 'bg-purple-300' : 'bg-gray-200'}`}></div>
                            {/* 阶段 2: 调用工具 */}
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${
                              agentPhase === 'tool_calling' ? 'bg-orange-50 border-orange-200 text-orange-700 font-medium' : 'text-gray-400'
                            }`}>
                              <span>🔧</span>
                              <span>工具</span>
                            </div>
                            {/* 连接线 */}
                            <div className={`w-4 h-px ${agentPhase === 'observing' || agentPhase === 'evaluating' || agentPhase === 'responding' ? 'bg-orange-300' : 'bg-gray-200'}`}></div>
                            {/* 阶段 3: 观察 */}
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${
                              agentPhase === 'observing' ? 'bg-blue-50 border-blue-200 text-blue-700 font-medium' : 'text-gray-400'
                            }`}>
                              <span>👀</span>
                              <span>观察</span>
                            </div>
                            {/* 连接线 */}
                            <div className={`w-4 h-px ${agentPhase === 'evaluating' || agentPhase === 'responding' ? 'bg-blue-300' : 'bg-gray-200'}`}></div>
                            {/* 阶段 4: 评估 */}
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${
                              agentPhase === 'evaluating' ? 'bg-indigo-50 border-indigo-200 text-indigo-700 font-medium' : 'text-gray-400'
                            }`}>
                              <span>🧠</span>
                              <span>评估</span>
                            </div>
                            {/* 连接线 */}
                            <div className={`w-4 h-px ${agentPhase === 'responding' ? 'bg-indigo-300' : 'bg-gray-200'}`}></div>
                            {/* 阶段 5: 回答 */}
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${
                              agentPhase === 'responding' ? 'bg-green-50 border-green-200 text-green-700 font-medium' : 'text-gray-400'
                            }`}>
                              <span>💬</span>
                              <span>回答</span>
                            </div>
                          </div>
                        </div>
                      )}

                      {/* ===== 思考过程（折叠面板） ===== */}
                      {displayThinking && (
                        <details className="mb-2" open={isCurrentAssistant(msg)}>
                          <summary className="text-xs text-purple-600 font-medium cursor-pointer hover:text-purple-800 select-none">
                            🧠 思考过程 {isCurrentAssistant(msg) && <span className="animate-pulse text-purple-400">▌</span>}
                          </summary>
                          <div className="mt-1 text-xs text-gray-500 bg-purple-50 rounded p-2 prose prose-sm max-w-none max-h-40 overflow-y-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {preprocessMarkdown(displayThinking)}
                            </ReactMarkdown>

                          </div>
                        </details>
                      )}

                      {/* ===== 工具调用记录 ===== */}
                      {displayToolCalls && displayToolCalls.length > 0 && (
                        <div className="mb-2">
                          <div className="text-xs text-orange-600 font-medium mb-1">🔧 工具调用</div>
                          {displayToolCalls.map((tc, idx) => (
                            <div key={idx} className="text-xs text-orange-700 bg-orange-50 rounded p-1.5 mb-1 font-mono">
                              <span className="font-medium">{tc.tool}</span>
                              {tc.args && Object.keys(tc.args).length > 0 && (
                                <span className="text-orange-500">
                                  {' '}({JSON.stringify(tc.args).slice(0, 100)})
                                </span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* ===== 观察结果（折叠面板） ===== */}
                      {displayObservations && (
                        <details className="mb-2" open={isCurrentAssistant(msg)}>
                          <summary className="text-xs text-blue-600 font-medium cursor-pointer hover:text-blue-800 select-none">
                            👀 观察结果 {isCurrentAssistant(msg) && <span className="animate-pulse text-blue-400">▌</span>}
                          </summary>
                          <div className="mt-1 text-xs text-gray-600 bg-blue-50 rounded p-2 prose prose-sm max-w-none max-h-32 overflow-y-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {preprocessMarkdown(displayObservations)}
                            </ReactMarkdown>

                          </div>
                        </details>
                      )}

                      {/* ===== 评估思考（折叠面板）- 独立于初始思考过程 ===== */}
                      {displayEvaluation && (
                        <details className="mb-2" open={isCurrentAssistant(msg)}>
                          <summary className="text-xs text-indigo-600 font-medium cursor-pointer hover:text-indigo-800 select-none">
                            🧠 评估思考 {isCurrentAssistant(msg) && <span className="animate-pulse text-indigo-400">▌</span>}
                          </summary>
                          <div className="mt-1 text-xs text-gray-600 bg-indigo-50 rounded p-2 prose prose-sm max-w-none max-h-40 overflow-y-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                              {preprocessMarkdown(displayEvaluation)}
                            </ReactMarkdown>

                          </div>
                        </details>
                      )}

                      {/* ===== 消息内容 - 正式回答（支持 Markdown 渲染） ===== */}
                      <div className="prose prose-sm max-w-none break-words">
                        {msg.content ? (
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            rehypePlugins={[rehypeHighlight]}
                          >
                            {preprocessMarkdown(msg.content)}
                          </ReactMarkdown>

                        ) : msg.role === 'assistant' && !isCurrentAssistant(msg) ? (
                          <span className="text-gray-400 italic">（空）</span>
                        ) : msg.role === 'assistant' && isCurrentAssistant(msg) ? (
                          <span className="text-gray-400 italic animate-pulse">思考中...</span>
                        ) : ''}
                      </div>

                      {/* 检索来源展示区域 */}
                      {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-gray-200">
                          <p className="text-xs text-gray-500 font-medium mb-1">📚 检索来源：</p>
                          {msg.sources.map((source, idx) => (
                            <div key={idx} className="text-xs text-gray-500 mb-0.5">
                              <span className="font-medium">{source.title}</span>
                              {source.content && (
                                <span className="text-gray-400"> - {source.content.slice(0, 50)}...</span>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* 时间戳 */}
                      <div
                        className={`text-xs mt-1 ${
                          msg.role === 'user' ? 'text-blue-200' : 'text-gray-400'
                        }`}
                      >
                        {new Date(msg.timestamp).toLocaleTimeString('zh-CN')}
                      </div>
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* 输入区域 */}
        <div className="flex-shrink-0">
          <div className="flex items-end gap-2">
            <div className="flex-1 relative">
              <textarea
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="请先选择知识库，再输入消息... (Enter 发送, Shift+Enter 换行)"
                className="w-full border rounded-lg px-4 py-2.5 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                rows={2}
                disabled={isLoading}
              />
            </div>
            <div className="flex gap-2 items-stretch">
              {isLoading ? (
                <button
                  onClick={handleStop}
                  className="px-4 h-16 bg-red-600 text-white rounded-lg hover:bg-red-700 transition flex items-center gap-1"
                >
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                    <rect x="4" y="4" width="12" height="12" rx="1" />
                  </svg>
                  停止
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={!inputValue.trim()}
                  className="px-4 h-16 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition disabled:opacity-50 flex items-center gap-1"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"
                    />
                  </svg>
                  发送
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Chat;
