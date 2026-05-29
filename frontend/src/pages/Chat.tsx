import { useState, useEffect, useRef, useCallback } from 'react';
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

function Chat() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [currentAgentType, setCurrentAgentType] = useState<'rag' | 'chat' | null>(null);

  // 对话列表相关
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentAssistantMsgRef = useRef<ChatMessage | null>(null);

  // 加载知识库列表
  useEffect(() => {
    listKnowledgeBases()
      .then((data) => setKnowledgeBases(data))
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
  }, [messages, scrollToBottom]);

  // 处理 SSE 事件
  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
    switch (event.type) {
      case 'agent':
        setCurrentAgentType(event.content as 'rag' | 'chat');
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.id === currentAssistantMsgRef.current?.id) {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...lastMsg,
              agentType: event.content as 'rag' | 'chat',
            };
            return updated;
          }
          return prev;
        });
        break;

      case 'conversation_id':
        setConversationId(event.conversationId || event.content || null);
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

      case 'done':
        setIsLoading(false);
        setCurrentAgentType(null);
        currentAssistantMsgRef.current = null;
        // 刷新对话列表
        loadConversations();
        break;

      case 'error':
        setIsLoading(false);
        setCurrentAgentType(null);
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
  }, [loadConversations]);

  // 发送消息
  const handleSend = useCallback(() => {
    const trimmed = inputValue.trim();
    if (!trimmed || isLoading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: Date.now(),
    };

    const assistantMsg: ChatMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    };

    currentAssistantMsgRef.current = assistantMsg;
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInputValue('');
    setIsLoading(true);
    setCurrentAgentType(null);

    abortControllerRef.current = sendChatMessage(
      trimmed,
      handleSSEEvent,
      selectedKbId || undefined,
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
    setCurrentAgentType(null);
    currentAssistantMsgRef.current = null;
  }, []);

  // 新对话
  const handleNewChat = useCallback(() => {
    handleStop();
    setMessages([]);
    setConversationId(null);
    setCurrentAgentType(null);
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
        const loadedMessages: ChatMessage[] = convData.messages.map((msg: any) => ({
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

  // 获取 Agent 类型标签
  const getAgentBadge = (agentType?: 'rag' | 'chat') => {
    if (!agentType) return null;
    const isRag = agentType === 'rag';
    return (
      <span
        className={`inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium ${
          isRag
            ? 'bg-purple-100 text-purple-800'
            : 'bg-green-100 text-green-800'
        }`}
      >
        {isRag ? 'RAG Agent' : 'Chat Agent'}
      </span>
    );
  };

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
            {currentAgentType && (
              <span
                className={`inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium animate-pulse ${
                  currentAgentType === 'rag'
                    ? 'bg-purple-100 text-purple-800'
                    : 'bg-green-100 text-green-800'
                }`}
              >
                {currentAgentType === 'rag' ? 'RAG Agent' : 'Chat Agent'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-500 font-medium whitespace-nowrap">📚 知识库</span>
            <div className="relative">
              <select
                value={selectedKbId}
                onChange={(e) => setSelectedKbId(e.target.value)}
                className={`appearance-none border-2 rounded-lg px-3 py-2 pr-8 text-sm font-medium transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 cursor-pointer ${
                  selectedKbId
                    ? 'border-blue-400 bg-blue-50 text-blue-700'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                }`}
                disabled={isLoading}
              >
                <option value="">全部知识库</option>
                {knowledgeBases.map((kb) => (
                  <option key={kb.id} value={kb.id}>
                    {kb.name}
                  </option>
                ))}
              </select>
              <div className="pointer-events-none absolute inset-y-0 right-0 flex items-center pr-2">
                <svg className={`w-4 h-4 transition-colors ${selectedKbId ? 'text-blue-500' : 'text-gray-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
              </div>
            </div>
            {selectedKbId && (
              <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
                已选择
              </span>
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
              {messages.map((msg) => (
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
                    {/* Agent 类型标识 */}
                    {msg.role === 'assistant' && msg.agentType && (
                      <div className="mb-1">
                        {getAgentBadge(msg.agentType)}
                      </div>
                    )}

                    {/* 消息内容 */}
                    <div className="whitespace-pre-wrap break-words">
                      {msg.content || (msg.role === 'assistant' ? (
                        <span className="text-gray-400 italic animate-pulse">思考中...</span>
                      ) : '')}
                    </div>

                    {/* 检索来源展示区域 */}
                    {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-gray-200">
                        <p className="text-xs text-gray-500 font-medium mb-1">检索来源：</p>
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
              ))}
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
                placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
                className="w-full border rounded-lg px-4 py-2.5 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                rows={2}
                disabled={isLoading}
              />
            </div>
            <div className="flex gap-2">
              {isLoading ? (
                <button
                  onClick={handleStop}
                  className="px-4 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition flex items-center gap-1"
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
                  className="px-4 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition disabled:opacity-50 flex items-center gap-1"
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
