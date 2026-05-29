import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器
api.interceptors.request.use(
  (config) => {
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器
api.interceptors.response.use(
  (response) => {
    return response.data;
  },
  (error) => {
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

// ==================== 知识库 API ====================

export interface KnowledgeBase {
  id: string;
  name: string;
  createdAt: string;
}

export function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  return api.get('/knowledge-bases') as Promise<KnowledgeBase[]>;
}

export function createKnowledgeBase(name: string): Promise<KnowledgeBase> {
  return api.post('/knowledge-bases', { name }) as Promise<KnowledgeBase>;
}

export function deleteKnowledgeBase(id: string): Promise<{ message: string }> {
  return api.delete(`/knowledge-bases/${id}`) as Promise<{ message: string }>;
}

export function updateKnowledgeBase(id: string, name: string): Promise<KnowledgeBase> {
  return api.put(`/knowledge-bases/${id}`, { name }) as Promise<KnowledgeBase>;
}

// ==================== 文档 API ====================

export interface DocumentItem {
  id: string;
  knowledgeBaseId: string;
  name: string;
  filePath: string;
  fileType: string;
  fileSize: number;
  status: string;
  summary?: string;
  createdAt: string;
}

export function listDocuments(kbId: string): Promise<DocumentItem[]> {
  return api.get(`/knowledge-bases/${kbId}/documents`) as Promise<DocumentItem[]>;
}

export function uploadDocument(kbId: string, file: File): Promise<DocumentItem> {
  const formData = new FormData();
  formData.append('file', file);
  return api.post(`/knowledge-bases/${kbId}/documents`, formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  }) as Promise<DocumentItem>;
}

export function deleteDocument(kbId: string, docId: string): Promise<{ message: string }> {
  return api.delete(`/knowledge-bases/${kbId}/documents/${docId}`) as Promise<{ message: string }>;
}

// ==================== 聊天 API ====================

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  agentType?: 'rag' | 'chat';
  sources?: { title: string; content: string }[];
  timestamp: number;
}

export interface SSEChatEvent {
  type: 'text' | 'agent' | 'conversation_id' | 'sources' | 'done' | 'error';
  content?: string;
  conversationId?: string;
}

/**
 * 发送聊天消息并通过 SSE 接收流式响应
 * @param message 用户消息
 * @param onEvent 事件回调
 * @param knowledgeBaseId 知识库ID（可选）
 * @param conversationId 对话ID（可选）
 * @returns AbortController 用于取消请求
 */
export function sendChatMessage(
  message: string,
  onEvent: (event: SSEChatEvent) => void,
  knowledgeBaseId?: string,
  conversationId?: string
): AbortController {
  const controller = new AbortController();

  // 使用 POST 请求，将参数放在请求体中，避免 URL 编码问题
  const body: Record<string, string> = { message };
  if (knowledgeBaseId) {
    body.knowledge_base_id = knowledgeBaseId;
  }
  if (conversationId) {
    body.conversation_id = conversationId;
  }

  const url = `/api/chat/stream`;
  console.log('[SSE] Starting POST fetch to:', url, 'body:', body);

  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/plain, */*',
      'Cache-Control': 'no-cache',
    },
    body: JSON.stringify(body),
    signal: controller.signal,
  })

    .then(async (response) => {
      console.log('[SSE] Response received, status:', response.status, 'ok:', response.ok);

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      console.log('[SSE] Reader:', reader ? 'available' : 'null');
      if (!reader) {
        throw new Error('No reader available');
      }

      const decoder = new TextDecoder();
      // buffer 用于存储跨 chunk 边界的部分行
      let buffer = '';
      let chunkCount = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          console.log('[SSE] Stream done, total chunks:', chunkCount);
          break;
        }

        chunkCount++;
        const text = decoder.decode(value, { stream: true });
        console.log(`[SSE] Chunk #${chunkCount}, length: ${text.length}`);
        buffer += text;

        // 按 SSE 事件格式解析：event:\ndata:\n\n
        // 使用正则匹配完整的 SSE 事件块
        // 使用 \r?\n 兼容 Windows (\r\n) 和 Unix (\n) 换行符
        const eventRegex = /event: (.+)\r?\ndata: (.+)\r?\n\r?\n/g;
        let match;

        // 保存匹配后剩余的未完成部分
        let lastIndex = 0;

        while ((match = eventRegex.exec(buffer)) !== null) {
          const eventType = match[1].trim();
          const rawData = match[2].trim();
          lastIndex = match.index + match[0].length;

          console.log(`[SSE] Matched event: ${eventType}, data: ${rawData.slice(0, 50)}`);

          // 尝试解析 data 中的 JSON
          try {
            const parsed = JSON.parse(rawData);
            console.log('[SSE] Event:', parsed.type, parsed.content?.slice(0, 50) || '');
            onEvent(parsed as SSEChatEvent);
          } catch (e) {
            console.warn('[SSE] Failed to parse data JSON:', rawData);
          }
        }

        // 保留未匹配的部分（可能是不完整的 SSE 事件块）
        if (lastIndex > 0) {
          buffer = buffer.slice(lastIndex);
        } else {
          // 如果没有匹配到任何完整事件，但 buffer 已经很大了，说明可能有问题
          if (buffer.length > 500) {
            console.warn('[SSE] Buffer too large without match, resetting. Buffer:', buffer.slice(0, 200));
            buffer = '';
          }
        }
      }
    })
    .catch((err) => {
      if (err.name === 'AbortError') {
        console.log('[SSE] Request aborted');
        return; // 用户取消
      }
      console.error('[SSE] Connection error:', err);
      onEvent({ type: 'error', content: '连接失败，请检查网络后重试。' });
    });

  return controller;
}

// ==================== 对话管理 API ====================

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
}

export interface ConversationListResponse {
  conversations: Conversation[];
}

/**
 * 获取所有对话列表
 */
export function listConversations(): Promise<ConversationListResponse> {
  return api.get('/conversations') as Promise<ConversationListResponse>;
}

/**
 * 获取单个对话及其所有消息
 */
export function getConversation(id: string): Promise<ConversationDetail> {
  return api.get(`/conversations/${id}`) as Promise<ConversationDetail>;
}

/**
 * 删除对话
 */
export function deleteConversation(id: string): Promise<{ success: boolean; message: string }> {
  return api.delete(`/conversations/${id}`) as Promise<{ success: boolean; message: string }>;
}

/**
 * 更新对话标题
 */
export function updateConversationTitle(id: string, title: string): Promise<{ success: boolean; message: string }> {
  return api.put(`/conversations/${id}/title`, { title }) as Promise<{ success: boolean; message: string }>;
}

export default api;
