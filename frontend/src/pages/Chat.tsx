import { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

/**
 * 预处理 Markdown 文本，修复 LLM 生成的不规范格式
 */
function preprocessMarkdown(text: string): string {
  if (!text) return text;
  let result = text;
  result = result.replace(/(^|\n)(#{1,6})(?!\s)(?!#)/gm, '$1$2 ');
  result = result.replace(/^(\s*[-*+])(?![\s\-*])/gm, '$1 ');
  result = result.replace(/^(\s*\d+\.)(?=\S)/gm, '$1 ');
  result = result.replace(/([\u4e00-\u9fff\u3002\uff1f\uff01\u3001])\s*-([\u4e00-\u9fff])/g, '$1\n- $2');
  result = result.replace(/([^\n])\n---\n([^\n])/g, '$1\n\n---\n\n$2');
  result = result.replace(/^>([^>\s])/gm, '> $1');
  result = result.replace(/([^\n#])(#{1,6})(\d)/g, '$1\n\n$2 $3');
  return result;
}

import {
  listKnowledgeBases,
  sendChatMessage,
  listConversations,
  getConversation,
  deleteConversation,
  getCurrentLLMConfig,
  updateLLMConfig,
  clearAllCache,
  KnowledgeBase,
  ChatMessage,
  SSEChatEvent,
  Conversation,
  LLMConfig,
} from '../api/index';

interface ExtendedChatMessage extends ChatMessage {
  thinking?: string;
  evaluation?: string;
  observations?: string;
  toolCalls?: { tool: string; args: any }[];
}

function Chat() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState<string>('');
  const [messages, setMessages] = useState<ExtendedChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [currentToolName, setCurrentToolName] = useState<string | null>(null);
  const [thinkingContent, setThinkingContent] = useState<string>('');
  const [evaluationContent, setEvaluationContent] = useState<string>('');
  const [observationContent, setObservationContent] = useState<string>('');
  const [agentPhase, setAgentPhase] = useState<'idle' | 'thinking' | 'tool_calling' | 'observing' | 'evaluating' | 'responding'>('idle');
  const [showKbDropdown, setShowKbDropdown] = useState(false);
  const kbDropdownRef = useRef<HTMLDivElement>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [showSidebar, setShowSidebar] = useState(true);
  const [llmConfig, setLlmConfig] = useState<LLMConfig | null>(null);
  const [showLlmConfigModal, setShowLlmConfigModal] = useState(false);
  const [configForm, setConfigForm] = useState({ api_key: '', api_base: '', model: '' });
  const [isSavingLlmConfig, setIsSavingLlmConfig] = useState(false);
  const [llmConfigMessage, setLlmConfigMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [cacheMessage, setCacheMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [clearingCache, setClearingCache] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const currentAssistantMsgRef = useRef<ExtendedChatMessage | null>(null);
  const thinkingContentRef = useRef<string>('');
  const evaluationContentRef = useRef<string>('');
  const observationContentRef = useRef<string>('');
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

  useEffect(() => {
    getCurrentLLMConfig()
      .then((config) => setLlmConfig(config))
      .catch((err) => console.error('Failed to load LLM config:', err));
  }, []);

  useEffect(() => {
    listKnowledgeBases()
      .then((data) => setKnowledgeBases(data))
      .catch((err) => console.error('Failed to load knowledge bases:', err));
  }, []);

  const loadConversations = useCallback(() => {
    listConversations()
      .then((data) => setConversations(data.conversations))
      .catch((err) => console.error('Failed to load conversations:', err));
  }, []);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  const scrollToBottom = useCallback(() => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 50);
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, thinkingContent, observationContent, scrollToBottom]);

  const handleSSEEvent = useCallback((event: SSEChatEvent) => {
    switch (event.type) {
      case 'agent':
        setCurrentToolName(event.content || null);
        break;
      case 'conversation_id':
        setConversationId(event.conversationId || event.content || null);
        break;
      case 'thinking':
        if (event.content) {
          setThinkingContent((prev) => prev + event.content);
          thinkingContentRef.current += event.content;
          setAgentPhase('thinking');
        }
        break;
      case 'evaluation':
        if (event.content) {
          setEvaluationContent((prev) => prev + event.content);
          evaluationContentRef.current += event.content;
          setAgentPhase('evaluating');
        }
        break;
      case 'tool_call':
        if (event.content) {
          toolCallsRef.current.push({ tool: event.content, args: event.args || {} });
        }
        setAgentPhase('tool_calling');
        break;
      case 'observation':
        if (event.content) {
          setObservationContent((prev) => prev + event.content);
          observationContentRef.current += event.content;
          setAgentPhase('observing');
        }
        break;
      case 'text':
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.role === 'assistant' && lastMsg.id === currentAssistantMsgRef.current?.id) {
            const updated = [...prev];
            updated[updated.length - 1] = { ...lastMsg, content: lastMsg.content + event.content };
            return updated;
          }
          return prev;
        });
        setAgentPhase((prev) => {
          if (prev === 'evaluating') {
            return evaluationContentRef.current ? 'responding' : 'evaluating';
          }
          return prev === 'observing' ? 'responding' : 'responding';
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
                updated[updated.length - 1] = { ...lastMsg, sources: Array.isArray(sources) ? sources : lastMsg.sources };
                return updated;
              }
              return prev;
            });
          } catch {}
        }
        break;
      case 'done': {
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
        loadConversations();
        break;
      }
      case 'error': {
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
          { id: `error-${Date.now()}`, role: 'assistant', content: event.content || '发生未知错误', timestamp: Date.now() },
        ]);
        currentAssistantMsgRef.current = null;
        break;
      }
    }
  }, [loadConversations]);

  const handleSend = useCallback(() => {
    const trimmed = inputValue.trim();
    if (!trimmed || isLoading) return;
    if (!selectedKbId) {
      setMessages((prev) => [
        ...prev,
        { id: `user-${Date.now()}`, role: 'user', content: trimmed, timestamp: Date.now() },
        { id: `hint-${Date.now()}`, role: 'assistant', content: '⚠️ 请先选择知识库', timestamp: Date.now() },
      ]);
      setInputValue('');
      return;
    }
    const userMsg: ExtendedChatMessage = { id: `user-${Date.now()}`, role: 'user', content: trimmed, timestamp: Date.now() };
    const assistantMsg: ExtendedChatMessage = { id: `assistant-${Date.now()}`, role: 'assistant', content: '', timestamp: Date.now() };
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
    abortControllerRef.current = sendChatMessage(trimmed, handleSSEEvent, selectedKbId, conversationId || undefined);
  }, [inputValue, isLoading, selectedKbId, conversationId, handleSSEEvent]);

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

  const handleSelectConversation = useCallback(async (convId: string) => {
    if (isLoading) return;
    handleStop();
    try {
      const convData = await getConversation(convId);
      if (convData) {
        // 直接使用后端返回的原始顺序（后端已按 created_at ASC, id ASC 排序），
        // 不再依赖前端 timestamp 排序，避免因 JS Date 精度不足（仅毫秒）导致微秒级时间差无法区分
        const loadedMessages: ExtendedChatMessage[] = convData.messages
          .map((msg: any) => ({
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

  const handleDeleteConversation = useCallback(async (e: React.MouseEvent, convId: string) => {
    e.stopPropagation();
    try {
      await deleteConversation(convId);
      if (convId === conversationId) {
        setMessages([]);
        setConversationId(null);
      }
      loadConversations();
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  }, [conversationId, loadConversations]);

  // 打开 DeepSeek 配置弹窗
  const handleOpenLlmConfig = useCallback(async () => {
    setLlmConfigMessage(null);
    try {
      const config = await getCurrentLLMConfig();
      setConfigForm({
        api_key: '',
        api_base: config.model ? '' : '',
        model: config.model,
      });
      setShowLlmConfigModal(true);
    } catch (err) {
      console.error('Failed to load LLM config:', err);
      setLlmConfigMessage({ type: 'error', text: '加载配置失败，请检查后端服务是否正常运行。' });
    }
  }, []);

  // 保存 DeepSeek 配置
  const handleSaveLlmConfig = useCallback(async () => {
    setIsSavingLlmConfig(true);
    setLlmConfigMessage(null);
    try {
      await updateLLMConfig({
        api_key: configForm.api_key || undefined,
        api_base: configForm.api_base || undefined,
        model: configForm.model || undefined,
      });
      setLlmConfigMessage({ type: 'success', text: 'DeepSeek 配置保存成功！' });
      const config = await getCurrentLLMConfig();
      setLlmConfig(config);
    } catch (err) {
      console.error('Failed to save LLM config:', err);
      setLlmConfigMessage({ type: 'error', text: '保存配置失败，请重试。' });
    } finally {
      setIsSavingLlmConfig(false);
    }
  }, [configForm]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  const formatTime = (dateStr: string | null | undefined) => {
    if (!dateStr) return '';
    const date = new Date(dateStr);
    // 检查日期是否有效
    if (isNaN(date.getTime())) return '';
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    if (diffDays === 1) return '昨天';
    if (diffDays < 7) return `${diffDays}天前`;
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  };

  const isCurrentAssistant = (msg: ExtendedChatMessage) => {
    return msg.role === 'assistant' && msg.id === currentAssistantMsgRef.current?.id;
  };

  const getThinkingContent = (msg: ExtendedChatMessage): string | null => {
    if (isCurrentAssistant(msg)) return thinkingContent || null;
    return msg.thinking || null;
  };

  const getEvaluationContent = (msg: ExtendedChatMessage): string | null => {
    if (isCurrentAssistant(msg)) return evaluationContent || null;
    return msg.evaluation || null;
  };

  const getObservationContent = (msg: ExtendedChatMessage): string | null => {
    if (isCurrentAssistant(msg)) return observationContent || null;
    return msg.observations || null;
  };

  const getToolCalls = (msg: ExtendedChatMessage): { tool: string; args: any }[] | null => {
    if (isCurrentAssistant(msg)) return toolCallsRef.current.length > 0 ? toolCallsRef.current : null;
    return msg.toolCalls || null;
  };

  // 清除所有缓存（一级 Redis + 二级 pgvector）
  const handleClearAllCache = useCallback(async () => {
    if (!window.confirm('确定要清空所有缓存（一级+二级）吗？')) return;
    setClearingCache(true);
    setCacheMessage(null);
    try {
      await clearAllCache();
      setCacheMessage({ type: 'success', text: '✅ 所有缓存已清空' });
    } catch (err) {
      console.error('Failed to clear cache:', err);
      setCacheMessage({ type: 'error', text: '❌ 清空缓存失败，请检查服务是否正常运行' });
    } finally {
      setClearingCache(false);
      setTimeout(() => setCacheMessage(null), 3000);
    }
  }, []);

  return (
    <div className="flex h-[calc(100vh-3rem)] gap-0">
      {/* 左侧对话列表侧边栏 */}
      <div className={`${showSidebar ? 'w-72' : 'w-0'} flex-shrink-0 transition-all duration-300 overflow-hidden border-r bg-white rounded-l-lg`}>
        <div className="flex flex-col h-full">
          <div className="p-3 border-b">
            <button onClick={handleNewChat} disabled={isLoading} className="w-full py-2 px-4 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition text-sm font-medium disabled:opacity-50">
              + 新对话
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {conversations.length === 0 ? (
              <div className="p-4 text-center text-gray-400 text-sm">暂无历史对话</div>
            ) : (
              <div className="py-1">
                {conversations.map((conv) => (
                  <div
                    key={conv.id}
                    onClick={() => handleSelectConversation(conv.id)}
                    className={`group flex items-center px-3 py-2.5 cursor-pointer transition-colors ${
                      conv.id === conversationId ? 'bg-blue-50 border-l-2 border-blue-600' : 'hover:bg-gray-50 border-l-2 border-transparent'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-gray-800 truncate">{conv.title}</div>
                      <div className="text-xs text-gray-400 mt-0.5">{conv.message_count} 条消息 · {formatTime(conv.updated_at)}</div>
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
        <div className="flex items-center justify-between mb-4 flex-shrink-0">
          <div className="flex items-center gap-3">
            <button onClick={() => setShowSidebar(!showSidebar)} className="p-1.5 rounded-lg hover:bg-gray-100 transition text-gray-500" title={showSidebar ? '隐藏对话列表' : '显示对话列表'}>
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {showSidebar ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M5 5l7 7-7 7" />
                )}
              </svg>
            </button>
            <h1 className="text-2xl font-bold">Agent</h1>
            {currentToolName && (
              <span className="inline-flex items-center text-xs px-2 py-0.5 rounded-full font-medium animate-pulse bg-orange-100 text-orange-800">
                {currentToolName}
              </span>
            )}
          </div>

          {/* DeepSeek 模型标识 + 配置按钮 */}
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 px-3 py-2 border-2 border-emerald-400 bg-emerald-50 text-emerald-700 rounded-lg text-sm font-medium">
              <span>🤖</span>
              <span>{llmConfig ? `LLM (${llmConfig.model})` : '加载中...'}</span>
            </div>
            <button
              onClick={handleOpenLlmConfig}
              className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              title="配置 DeepSeek API"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </button>

            {/* 清空所有缓存按钮（一级+二级） */}
            <button
              onClick={handleClearAllCache}
              disabled={clearingCache}
              className="p-2 text-purple-500 hover:text-purple-700 hover:bg-purple-50 rounded-lg transition-colors disabled:opacity-50"
              title="清空所有缓存（一级+二级）"
            >
              {clearingCache ? (
                <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              )}
            </button>
          </div>

          {/* 知识库选择按钮 */}
          <div className="relative" ref={kbDropdownRef}>
            <button
              onClick={() => setShowKbDropdown(!showKbDropdown)}
              disabled={isLoading}
              className="flex items-center gap-2 px-3 py-2 border-2 border-blue-400 bg-blue-50 text-blue-700 rounded-lg text-sm font-medium hover:bg-blue-100 transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            >
              <span>📚</span>
              <span>{selectedKbId ? knowledgeBases.find(kb => kb.id === selectedKbId)?.name || '请选择知识库' : '请选择知识库'}</span>
              <svg className={`w-4 h-4 text-blue-500 transition-transform duration-200 ${showKbDropdown ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {showKbDropdown && (
              <div className="absolute right-0 mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-50 overflow-hidden">
                {knowledgeBases.length === 0 ? (
                  <div className="px-3 py-3 text-sm text-gray-400 text-center">暂无知识库</div>
                ) : (
                  knowledgeBases.map((kb) => (
                    <div
                      key={kb.id}
                      onClick={() => { setSelectedKbId(kb.id); setShowKbDropdown(false); }}
                      className={`flex items-center justify-between px-3 py-2.5 cursor-pointer text-sm transition-colors ${kb.id === selectedKbId ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-700 hover:bg-gray-50'}`}
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
              <p className="text-lg mb-2">Rag&Summarize&Guide</p>
              <p className="text-sm">选择知识库后发送消息，与 AI 聊天</p>
            </div>
          ) : (
            <div className="space-y-4">
              {messages.map((msg) => {
                const displayThinking = getThinkingContent(msg);
                const displayEvaluation = getEvaluationContent(msg);
                const displayObservations = getObservationContent(msg);
                const displayToolCalls = getToolCalls(msg);
                return (
                  <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                    <div className={`max-w-[80%] rounded-lg px-4 py-2 ${msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-900'}`}>
                      {msg.role === 'assistant' && isCurrentAssistant(msg) && agentPhase !== 'idle' && (
                        <div className="mb-3">
                          <div className="flex items-center gap-2 text-xs">
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${agentPhase === 'thinking' ? 'bg-purple-50 border-purple-200 text-purple-700 font-medium' : 'text-gray-400'}`}>
                              <span>🧠</span><span>思考</span>
                            </div>
                            <div className={`w-4 h-px ${agentPhase === 'tool_calling' || agentPhase === 'observing' || agentPhase === 'evaluating' || agentPhase === 'responding' ? 'bg-purple-300' : 'bg-gray-200'}`}></div>
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${agentPhase === 'tool_calling' ? 'bg-orange-50 border-orange-200 text-orange-700 font-medium' : 'text-gray-400'}`}>
                              <span>🔧</span><span>工具</span>
                            </div>
                            <div className={`w-4 h-px ${agentPhase === 'observing' || agentPhase === 'evaluating' || agentPhase === 'responding' ? 'bg-orange-300' : 'bg-gray-200'}`}></div>
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${agentPhase === 'observing' ? 'bg-blue-50 border-blue-200 text-blue-700 font-medium' : 'text-gray-400'}`}>
                              <span>👀</span><span>观察</span>
                            </div>
                            <div className={`w-4 h-px ${agentPhase === 'evaluating' || agentPhase === 'responding' ? 'bg-blue-300' : 'bg-gray-200'}`}></div>
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${agentPhase === 'evaluating' ? 'bg-indigo-50 border-indigo-200 text-indigo-700 font-medium' : 'text-gray-400'}`}>
                              <span>🧠</span><span>评估</span>
                            </div>
                            <div className={`w-4 h-px ${agentPhase === 'responding' ? 'bg-indigo-300' : 'bg-gray-200'}`}></div>
                            <div className={`flex items-center gap-1 px-2 py-1 rounded-full border ${agentPhase === 'responding' ? 'bg-green-50 border-green-200 text-green-700 font-medium' : 'text-gray-400'}`}>
                              <span>💬</span><span>回答</span>
                            </div>
                          </div>
                        </div>
                      )}

                      {displayThinking && (
                        <details className="mb-2" open={isCurrentAssistant(msg)}>
                          <summary className="text-xs text-purple-600 font-medium cursor-pointer hover:text-purple-800 select-none">
                            🧠 思考过程 {isCurrentAssistant(msg) && <span className="animate-pulse text-purple-400">▌</span>}
                          </summary>
                          <div className="mt-1 text-xs text-gray-500 bg-purple-50 rounded p-2 prose prose-sm max-w-none max-h-40 overflow-y-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{preprocessMarkdown(displayThinking)}</ReactMarkdown>
                          </div>
                        </details>
                      )}

                      {displayToolCalls && displayToolCalls.length > 0 && (
                        <div className="mb-2">
                          <div className="text-xs text-orange-600 font-medium mb-1">🔧 工具调用</div>
                          {displayToolCalls.map((tc, idx) => (
                            <div key={idx} className="text-xs text-orange-700 bg-orange-50 rounded p-1.5 mb-1 font-mono">
                              <span className="font-medium">{tc.tool}</span>
                              {tc.args && Object.keys(tc.args).length > 0 && <span className="text-orange-500"> ({JSON.stringify(tc.args).slice(0, 100)})</span>}
                            </div>
                          ))}
                        </div>
                      )}

                      {displayObservations && (
                        <details className="mb-2" open={isCurrentAssistant(msg)}>
                          <summary className="text-xs text-blue-600 font-medium cursor-pointer hover:text-blue-800 select-none">
                            👀 观察结果 {isCurrentAssistant(msg) && <span className="animate-pulse text-blue-400">▌</span>}
                          </summary>
                          <div className="mt-1 text-xs text-gray-600 bg-blue-50 rounded p-2 prose prose-sm max-w-none max-h-32 overflow-y-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{preprocessMarkdown(displayObservations)}</ReactMarkdown>
                          </div>
                        </details>
                      )}

                      {displayEvaluation && (
                        <details className="mb-2" open={isCurrentAssistant(msg)}>
                          <summary className="text-xs text-indigo-600 font-medium cursor-pointer hover:text-indigo-800 select-none">
                            🧠 评估思考 {isCurrentAssistant(msg) && <span className="animate-pulse text-indigo-400">▌</span>}
                          </summary>
                          <div className="mt-1 text-xs text-gray-600 bg-indigo-50 rounded p-2 prose prose-sm max-w-none max-h-40 overflow-y-auto">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{preprocessMarkdown(displayEvaluation)}</ReactMarkdown>
                          </div>
                        </details>
                      )}

                      <div className="prose prose-sm max-w-none break-words">
                        {msg.content ? (
                          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                            {preprocessMarkdown(msg.content)}
                          </ReactMarkdown>
                        ) : msg.role === 'assistant' && !isCurrentAssistant(msg) ? (
                          <span className="text-gray-400 italic">（空）</span>
                        ) : msg.role === 'assistant' && isCurrentAssistant(msg) ? (
                          <span className="text-gray-400 italic animate-pulse">思考中...</span>
                        ) : ''}
                      </div>

                      {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
                        <div className="mt-2 pt-2 border-t border-gray-200">
                          <p className="text-xs text-gray-500 font-medium mb-1">📚 检索来源：</p>
                          {msg.sources.map((source, idx) => (
                            <div key={idx} className="text-xs text-gray-500 mb-0.5">
                              <span className="font-medium">{source.title}</span>
                              {source.content && <span className="text-gray-400"> - {source.content.slice(0, 50)}...</span>}
                            </div>
                          ))}
                        </div>
                      )}

                      <div className={`text-xs mt-1 ${msg.role === 'user' ? 'text-blue-200' : 'text-gray-400'}`}>
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
                placeholder="请先选择知识库，再输入消息，你可以让我帮你总结知识库中的文档、回答关于知识库的问题，或者想知道可以对知识库文档提问什么问题等..."
                className="w-full border rounded-lg px-4 py-2.5 pr-12 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                rows={2}
                disabled={isLoading}
              />
            </div>
            <div className="flex gap-2 items-stretch">
              {isLoading ? (
                <button
                  onClick={handleStop}
                  className="px-4 py-2.5 bg-red-500 text-white rounded-lg hover:bg-red-600 transition text-sm font-medium flex items-center gap-1.5"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 10a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
                  </svg>
                  停止
                </button>
              ) : (
                <button
                  onClick={handleSend}
                  disabled={!inputValue.trim()}
                  className="px-4 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19V5m0 0l-7 7m7-7l7 7" />
                  </svg>
                  发送
                </button>
              )}
            </div>
          </div>
        </div>

        {/* DeepSeek 配置弹窗 */}
        {showLlmConfigModal && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-xl shadow-2xl p-6 w-full max-w-md mx-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-gray-900">DeepSeek API 配置</h2>
                <button
                  onClick={() => setShowLlmConfigModal(false)}
                  className="p-1 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>

              {llmConfigMessage && (
                <div className={`mb-4 p-3 rounded-lg text-sm ${
                  llmConfigMessage.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'
                }`}>
                  {llmConfigMessage.text}
                </div>
              )}

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">API Key</label>
                  <input
                    type="password"
                    value={configForm.api_key}
                    onChange={(e) => setConfigForm({ ...configForm, api_key: e.target.value })}
                    placeholder="输入新的 API Key（留空则保持当前值）"
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">API Base URL</label>
                  <input
                    type="text"
                    value={configForm.api_base}
                    onChange={(e) => setConfigForm({ ...configForm, api_base: e.target.value })}
                    placeholder="输入新的 API Base URL（留空则保持当前值）"
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">模型名称</label>
                  <input
                    type="text"
                    value={configForm.model}
                    onChange={(e) => setConfigForm({ ...configForm, model: e.target.value })}
                    placeholder="输入新的模型名称（留空则保持当前值）"
                    className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>

              <div className="flex justify-end gap-3 mt-6">
                <button
                  onClick={() => setShowLlmConfigModal(false)}
                  className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition"
                >
                  取消
                </button>
                <button
                  onClick={handleSaveLlmConfig}
                  disabled={isSavingLlmConfig}
                  className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 transition disabled:opacity-50 flex items-center gap-2"
                >
                  {isSavingLlmConfig ? (
                    <>
                      <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      保存中...
                    </>
                  ) : '保存配置'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 缓存操作提示消息 */}
        {cacheMessage && (
          <div className={`fixed bottom-4 right-4 p-3 rounded-lg shadow-lg z-50 text-sm ${
            cacheMessage.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'
          }`}>
            {cacheMessage.text}
          </div>
        )}
      </div>
    </div>
  );
}

export default Chat;
