# 1

我已经找到问题了。PostgreSQL 数据库在 Docker 中运行正常，从宿主机可以成功连接。

问题根因： document-service 连接数据库的配置是 jdbc:postgresql://172.28.0.3:5432/rag_kb，但这个 IP 地址可能与你的 Java 应用运行环境不匹配。

请确认你的 Java document-service 是如何运行的：

如果 Java 应用也在 Docker 容器中运行 → 需要使用 Docker 网络内部的服务名 postgres 而不是 IP 地址 172.28.0.3

如果 Java 应用在宿主机上直接运行（非 Docker） → 需要使用 localhost 而不是 172.28.0.3

# 2

### 1. BGE-M3 使用 GPU

修改 `python/modules/document_processor/embedder.py` 第 11 行，将 `_device = "cpu"` 改为 `_device = "cuda"`（如果有 NVIDIA GPU）或 `_device = "mps"`（如果是 Apple Silicon）。

### 2. 前端文档状态实时更新

当前问题：上传文档后前端只显示初始状态 `uploaded`（"待处理"），因为文档处理是异步的（Java → Redis Stream → Python），前端不会自动刷新状态。

解决方案：在 `KnowledgeBase.tsx` 中添&#x52A0;__&#x8F6E;询机制__，对展开的知识库定期刷新文档列表（例如每 3 秒查询一次），直到所有文档的状态不再是 `uploaded` 或 `processing`。

具体改动：

- 在 `toggleExpand` 加载文档后，启动一个 `setInterval` 每 3 秒轮询一次
- 当所有文档的状态都是 `completed` 或 `error` 时停止轮询
- 组件卸载或折叠知识库时清除定时器

# 3

## 问题分析结果

经过对完整请求链路的分析，我找到了问题&#x7684;__&#x6839;本原因__。

### 请求链路

```javascript
前端 → Nginx → Gateway → Chat Service (Java) → Python SSE → DeepSeek API
```

### 根因：Java ChatController 的 SSE 双重包装问题

在 `ChatController.java` 中，代码使用 `bodyToFlux(String.class)` 直接透传 Python 返回的 SSE 原始文本：

```java
return pythonWebClient.get()
        .uri(url)
        .accept(MediaType.TEXT_EVENT_STREAM)
        .retrieve()
        .bodyToFlux(String.class)  // ← 问题在这里
```

同时 Controller 方法声明了 `produces = MediaType.TEXT_EVENT_STREAM_VALUE`。

__问题在于：__

1. Python 返回的 SSE 格式是：

```javascript
event: agent
data: {"type":"agent","content":"chat"}

event: message
data: {"type":"text","content":"你好"}
```

2. `bodyToFlux(String.class)` 将 Python 的原始 SSE 文本作为普通字符串发射

3. Spring WebFlux 的 SSE 渲染器将每个字符串包装为 `data:` 行，导致前端收到的&#x662F;__&#x53CC;重包&#x88C5;__&#x7684; SSE：

```javascript
data: event: agent
data: data: {"type":"agent","content":"chat"}
data: 
data: 
```

4. 前端的 SSE 解析器无法解析这种格式，因此收不到任何有效事件，一直显示"思考中"

### 修复方案

修改 `ChatController.java`，使用 `exchangeToFlux` 手动解析 Python 的 SSE 流，提取 `data:` 行中的 JSON 内容，然后使用 `Flux<ServerSentEvent<String>>` 重新生成正确的 SSE 格式。

具体改动：

1. 使用 `exchangeToFlux` 替代 `retrieve().bodyToFlux()`
2. 手动解析 SSE 行，提取 `data:` 中的 JSON
3. 使用 `ServerSentEvent` 构建器重新发射事件

### 需要修改的文件

__`java/chat-service/src/main/java/com/rag/chat/controller/ChatController.java`__ — 重写 SSE 流处理逻辑
