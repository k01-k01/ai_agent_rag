# RAG 系统性能优化 TOP 10

> 基于对项目 Python 侧和 Java 侧全部核心代码的深度审查
> 
> 审查日期：2026-05-28
> 
> ⚠️ 本文档已修正原 `performance_optimization_plan.md` 中的错误（索引已存在、模型复用方案不可行等）

---

## 目录

1. [TOP 10 优化项总览](#一top-10-优化项总览)
2. [各优化项详细说明](#二各优化项详细说明)
3. [快速见效 TOP 3](#三快速见效-top-3)
4. [实施路线图](#四实施路线图)

---

## 一、TOP 10 优化项总览

| 排名 | 优化项 | 所属侧 | 优先级 | 预期提升 | 改动量 | 风险 |
|:----:|--------|:------:|:------:|---------|:------:|:----:|
| 1 | **Router 关键词前置 + 逻辑修复** | Python | 🔴 | 减少 50-70% LLM 路由调用 | ⭐ 极小 | 低 |
| 2 | **并行混合检索** | Python | 🔴 | 检索速度提升 30-50% | ⭐ 极小 | 低 |
| 3 | **对话历史按需加载** | Python | 🔴 | 减少 60%+ 数据传输 | ⭐ 极小 | 低 |
| 4 | **BGE-M3 模型统一加载（chunker 复用 GPU 模型）** | Python | 🔴 | 节省 2.2GB 显存，语义切分提速 10-20x | ⭐⭐ 中等 | 中 |
| 5 | **共享数据库连接池** | Python | 🔴 | 减少 60% 连接创建开销 | ⭐⭐ 中等 | 中 |
| 6 | **异步任务并发控制** | Python | 🔴 | 避免高并发下内存/连接耗尽 | ⭐ 极小 | 低 |
| 7 | **Consumer 批量处理文档** | Python | 🟡 | 文档处理吞吐量提升 3-5x | ⭐⭐ 中等 | 中 |
| 8 | **对话保存合并为批量操作** | Python | 🟡 | 减少 50% 数据库往返 | ⭐ 极小 | 低 |
| 9 | **ChatController DataBuffer 优化** | Java | 🟡 | 减少 GC 压力，提升高并发吞吐 | ⭐ 极小 | 低 |
| 10 | **Gateway 熔断/限流/压缩** | Java | 🟡 | 提升系统稳定性 | ⭐⭐ 中等 | 中 |

### 优先级说明

- **🔴 高优先级**: 直接影响用户体验或系统稳定性，建议优先实施
- **🟡 中优先级**: 提升系统效率或可维护性，可在高优先级完成后实施
- **改动量**: ⭐（极小，<10行） ⭐⭐（中等，10-50行） ⭐⭐⭐（较大，50+行）

---

## 二、各优化项详细说明

---

### 🥇 TOP 1：Router 关键词前置 + 逻辑修复

#### 问题描述

当前路由流程存在**两个问题**：

**问题 A — 性能问题**：每次聊天都先调用 DeepSeek API 做路由判断，即使只是简单问候。

```
当前流程: 用户输入 → LLM 路由判断 (~1s) → 关键词兜底检查 → 执行
优化流程: 用户输入 → 关键词/规则前置检查 (~1ms) → LLM 路由判断（仅未命中规则时）→ 执行
```

**问题 B — 逻辑缺陷**：关键词兜底检查只在 `route_type == "chat"` 时才执行（`main.py:184`），这意味着：
- 如果 LLM 错误地返回了 `"rag"`，关键词检查不会触发
- 如果 LLM 返回了 `"chat"` 但用户问题包含知识库关键词，才会被纠正
- **反向情况（LLM 返回 rag 但实际是闲聊）不会被纠正**

#### 涉及文件

- `python/main.py`（第 176-196 行）
- `python/modules/agents/router.py`

#### 优化方案

```python
# 优化后的流程
# 1. 关键词前置检查（快速路径，约 1ms）
route_type = keyword_pre_check(message, knowledge_base_id)

# 2. 仅当关键词检查无法确定时，才调用 LLM
if route_type is None:
    router = get_router()
    route_type = await router.route(message, knowledge_base_id)

# 3. 移除原有的关键词兜底逻辑（已在步骤 1 中完成）
```

**关键词前置检查规则**：
- 问候语（"你好"、"hi"、"hello"等）→ 直接返回 `"chat"`
- 明确的知识库关键词（"根据知识库"、"文档中"等）→ 直接返回 `"rag"`
- 其他 → 返回 `None`，交给 LLM 判断

#### 预期效果

- 减少 **50-70%** 的 LLM 路由调用
- 修复关键词检查只在 chat 分支执行的逻辑缺陷
- 简单问候响应速度提升 **~1s**

---

### 🥇 TOP 2：并行混合检索

#### 问题描述

`fulltext_search` 和 `vector_search` 是完全独立的两个操作，但当前串行执行。

```python
# 当前 - 串行，耗时 = T_fulltext + T_vector
fulltext_results = await self.fulltext_search(query, kb_id)
vector_results = await self.vector_search(query, kb_id)
```

#### 涉及文件

- `python/modules/retrieval/hybrid_retriever.py`（第 448-454 行）

#### 优化方案

```python
# 优化 - 并行，耗时 = max(T_fulltext, T_vector)
fulltext_results, vector_results = await asyncio.gather(
    self.fulltext_search(query, kb_id),
    self.vector_search(query, kb_id)
)
```

#### 预期效果

- 检索速度提升 **30-50%**
- 改动仅 2 行代码

---

### 🥇 TOP 3：对话历史按需加载

#### 问题描述

`get_conversation` 加载所有消息，但 chat_agent 只取最近 40 条，rag_agent 只取最近 10 条。长对话中大量无用数据传输。

#### 涉及文件

- `python/modules/conversation/manager.py`（第 95-103 行）

#### 优化方案

在 SQL 查询中添加 `LIMIT` 子句：

```python
# 当前 - 加载全部消息
msg_rows = await conn.fetch(
    """
    SELECT id, role, content, agent_type, sources, created_at
    FROM conversation_messages
    WHERE conversation_id = $1
    ORDER BY created_at ASC
    """,
    conversation_id,
)

# 优化 - 只加载最近 40 条（覆盖 chat_agent 和 rag_agent 的最大需求）
msg_rows = await conn.fetch(
    """
    SELECT id, role, content, agent_type, sources, created_at
    FROM conversation_messages
    WHERE conversation_id = $1
    ORDER BY created_at ASC
    LIMIT 40
    """,
    conversation_id,
)
```

#### 预期效果

- 长对话（100+ 条消息）减少 **60%+** 的数据传输
- 减少 Python 对象创建和 JSON 序列化开销

---

### 🥇 TOP 4：BGE-M3 模型统一加载（chunker 复用 GPU 模型）

#### 问题描述

BGE-M3 模型被加载了**两次**，且 chunker 强制使用 CPU：

| 模块 | 文件 | 加载方式 | 设备 |
|------|------|---------|------|
| 嵌入生成 | `embedder.py` | `SentenceTransformer` | **GPU**（如果可用） |
| 语义切分 | `chunker.py` | `HuggingFaceBgeEmbeddings` | **强制 CPU** |

- **内存浪费**：两个模型实例占用双倍显存/内存（BGE-M3 约 2.2GB）
- **chunker 强制 CPU**：语义切分极慢（比 GPU 慢 10-20 倍）
- **加载两次**：增加应用启动时间

> ⚠️ **关于模型加载频率的说明**：
> - BGE-M3 使用全局单例模式，**只在首次调用时加载一次**，之后常驻内存
> - 但它在**每次用户请求**时都会被调用做推理（向量化用户问题用于检索和缓存匹配）
> - 并非"只在文档上传时加载"，而是**每次聊天请求都会用到**

#### 涉及文件

- `python/modules/document_processor/embedder.py`
- `python/modules/document_processor/chunker.py`

#### 优化方案

由于 `chunker.py` 的 `HuggingFaceBgeEmbeddings` 是 LangChain 的 `Embeddings` 接口，而 `embedder.py` 的 `SentenceTransformer` 不是，不能直接替换。需要创建一个适配器：

```python
# 在 chunker.py 中新增适配器类
from modules.document_processor.embedder import generate_embedding

class SharedBgeEmbeddings:
    """
    适配器：将 embedder.py 的 SentenceTransformer 包装为 LangChain Embeddings 接口
    使得 chunker.py 可以复用 embedder.py 中已经在 GPU 上的模型
    """
    def embed_documents(self, texts):
        return [generate_embedding(t) for t in texts]
    
    def embed_query(self, text):
        return generate_embedding(text)
```

然后在 `_get_embeddings()` 中返回适配器实例，而不是独立加载 `HuggingFaceBgeEmbeddings`：

```python
def _get_embeddings():
    global _embeddings
    if _embeddings is None:
        _embeddings = SharedBgeEmbeddings()  # 复用 embedder.py 的 GPU 模型
        logger.info("Using shared BGE-M3 model from embedder.py")
    return _embeddings
```

#### 预期效果

- 节省 **2.2GB** 显存/内存（减少一个完整的 BGE-M3 模型实例）
- 语义切分速度提升 **10-20x**（从 CPU 切换到 GPU）
- 减少应用启动时间（避免第二次模型加载）

---

### 🥇 TOP 5：共享数据库连接池

#### 问题描述

四个模块各自独立创建连接池，互不共享：

| 模块 | 文件 | 配置 |
|------|------|------|
| 语义缓存 | `semantic_cache.py` | min=1, max=5 |
| 混合检索 | `hybrid_retriever.py` | min=1, max=5 |
| 对话管理 | `conversation/manager.py` | min=1, max=5 |
| 文档处理 | `consumer.py` | min=2, max=5 |

- 总共可能创建 4 个独立连接池，浪费连接资源
- 单个池 max_size=5 在高并发下成为瓶颈
- 每个池独立管理生命周期，增加维护复杂度

#### 涉及文件

- `python/modules/cache/semantic_cache.py`
- `python/modules/retrieval/hybrid_retriever.py`
- `python/modules/conversation/manager.py`
- `python/modules/document_processor/consumer.py`

#### 优化方案

创建全局共享的数据库连接池模块 `python/db_pool.py`：

```python
"""
全局共享数据库连接池
"""
import asyncpg
from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

_pool = None

async def get_db_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=20,  # 根据并发需求调整
            command_timeout=30,
        )
    return _pool

async def close_db_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
```

所有模块统一从共享池获取连接，删除各自的 `_get_pool()` 实现。

#### 预期效果

- 减少 **60%** 的连接创建开销
- 提高连接利用率，支持更高并发
- 统一管理连接池生命周期

---

### 🥇 TOP 6：异步任务并发控制

#### 问题描述

`asyncio.create_task` 用于缓存写入和 Java 通知，但没有限制并发数：

```python
# 当前代码 - 无限制创建任务
asyncio.create_task(semantic_cache.set_cached_answer(...))
asyncio.create_task(semantic_cache.notify_java_set_cache(...))
```

高并发下可能创建大量任务导致：
- 内存飙升
- Redis/数据库连接耗尽
- HTTP 连接池耗尽

#### 涉及文件

- `python/main.py`（第 136-143、249-254 行）

#### 优化方案

```python
# 创建全局信号量
_cache_semaphore = asyncio.Semaphore(10)

# 使用信号量控制并发
async with _cache_semaphore:
    asyncio.create_task(semantic_cache.set_cached_answer(...))
```

或使用更优雅的任务队列 + worker 模式。

#### 预期效果

- 避免高并发下内存飙升
- 保护下游服务（Redis、Java API）不被突发流量打满

---

### 🥇 TOP 7：Consumer 批量处理文档

#### 问题描述

`start_consumer` 每次只处理 1 条消息（`count=1`），处理期间无法接收新消息。

#### 涉及文件

- `python/modules/document_processor/consumer.py`（第 176-181 行）

#### 优化方案

```python
# 当前 - 每次只读 1 条
results = await redis.xreadgroup(
    ...,
    count=1,
    ...
)

# 优化 - 批量读取，并发处理
results = await redis.xreadgroup(
    ...,
    count=5,  # 批量读取
    ...
)

# 使用 asyncio.gather 并发处理多个文档
tasks = []
for stream_name, messages in results:
    for message_id, fields in messages:
        tasks.append(process_message(message_id, fields))
await asyncio.gather(*tasks)
```

#### 预期效果

- 文档处理吞吐量提升 **3-5x**
- 更好地利用 CPU 和 GPU 资源

---

### 🥇 TOP 8：对话保存合并为批量操作

#### 问题描述

每次聊天响应完成后，`add_message` 被调用两次（user + assistant），每次都是独立事务：

```python
# 两次独立数据库操作，每次包含 INSERT + UPDATE
await conv_manager.add_message(conversation_id, "user", message)
await conv_manager.add_message(conversation_id, "assistant", full_response, ...)
```

每次 `add_message` 执行：
1. `INSERT INTO conversation_messages`
2. `UPDATE conversations SET updated_at = NOW()`

两次调用 = 4 条 SQL 语句，2 次事务提交。

#### 涉及文件

- `python/modules/conversation/manager.py`（第 157-205 行）
- `python/main.py`（第 163-167、240-244 行）

#### 优化方案

```python
# 新增批量添加消息方法
async def add_messages_batch(self, conversation_id: str, messages: list[dict]) -> bool:
    """批量添加消息，只更新一次 updated_at"""
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for msg in messages:
                    sources_json = json.dumps(msg.get("sources"), ensure_ascii=False) if msg.get("sources") else None
                    await conn.execute(
                        """
                        INSERT INTO conversation_messages 
                        (conversation_id, role, content, agent_type, sources)
                        VALUES ($1, $2, $3, $4, $5)
                        """,
                        conversation_id,
                        msg["role"],
                        msg["content"],
                        msg.get("agent_type"),
                        sources_json,
                    )
                # 只更新一次 updated_at
                await conn.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
                    conversation_id,
                )
        return True
    except Exception as e:
        logger.error(f"Error adding messages batch: {e}")
        return False
```

#### 预期效果

- 减少 **50%** 的数据库往返次数
- 减少 **50%** 的事务提交开销

---

### 🥇 TOP 9：ChatController DataBuffer 优化

#### 问题描述

每个 DataBuffer 都创建新的 byte 数组并手动 release：

```java
return response.bodyToFlux(DataBuffer.class)
    .map(buffer -> {
        byte[] bytes = new byte[buffer.readableByteCount()];  // 频繁分配
        buffer.read(bytes);
        DataBufferUtils.release(buffer);  // 手动 release
        return new String(bytes, StandardCharsets.UTF_8);
    });
```

频繁的 byte 数组分配和 GC 在高并发下成为瓶颈。

#### 涉及文件

- `java/chat-service/.../controller/ChatController.java`（第 140-148 行）

#### 优化方案

```java
// 优化方案 1：使用 bodyToFlux(String.class) 替代手动 DataBuffer 处理
return response.bodyToFlux(String.class);

// 优化方案 2：如果 bodyToFlux(String.class) 有兼容问题，使用 buffer.asByteBuffer()
return response.bodyToFlux(DataBuffer.class)
    .map(buffer -> {
        String chunk = StandardCharsets.UTF_8.decode(buffer.asByteBuffer()).toString();
        DataBufferUtils.release(buffer);
        return chunk;
    });
```

**推荐方案 1**，因为 Python 侧返回的已经是完整的 SSE 格式字符串，不需要在 Java 侧做任何解析。

#### 预期效果

- 减少 GC 压力
- 代码更简洁
- 高并发下吞吐量提升

---

### 🥇 TOP 10：Gateway 熔断/限流/压缩

#### 问题描述

Gateway 直接路由到固定地址，没有任何容错机制：

```yaml
routes:
  - id: chat-service
    uri: http://localhost:8082  # 硬编码地址
```

- 没有负载均衡（无法水平扩展）
- 没有熔断机制（下游服务故障会级联）
- 没有重试机制（临时故障导致请求失败）
- 没有响应压缩（浪费带宽）

#### 涉及文件

- `java/gateway/src/main/resources/application.yml`

#### 优化方案

```yaml
spring:
  cloud:
    gateway:
      # ... 现有路由配置 ...
      default-filters:
        - name: RequestRateLimiter
          args:
            redis-rate-limiter.replenishRate: 100
            redis-rate-limiter.burstCapacity: 200
        - name: Retry
          args:
            retries: 3
            statuses: BAD_GATEWAY, SERVICE_UNAVAILABLE, GATEWAY_TIMEOUT
            methods: GET, POST
            backoff:
              firstBackoff: 500ms
              maxBackoff: 5s
              factor: 2
      routes:
        - id: chat-service
          uri: lb://chat-service  # 使用负载均衡
          predicates:
            - Path=/api/chat/**
          filters:
            - name: CircuitBreaker
              args:
                name: chatServiceCircuitBreaker
                fallbackUri: forward:/fallback/chat
```

#### 预期效果

- 提升系统稳定性
- 防止级联故障
- 支持水平扩展

---

## 三、快速见效 TOP 3

如果时间有限，建议优先实施以下 3 项优化，每项改动不超过 5 行代码：

| 排名 | 优化项 | 改动行数 | 预期效果 |
|:----:|--------|:--------:|---------|
| 🥇 | Router 关键词前置 | ~10 行 | 减少 50-70% LLM 路由调用 |
| 🥇 | 并行混合检索 | ~2 行 | 检索速度提升 30-50% |
| 🥇 | 对话历史按需加载 | ~1 行 | 减少 60%+ 数据传输 |

---

## 四、实施路线图

### 第一阶段：快速见效（1-2 天）

1. **Router 关键词前置 + 逻辑修复** — 修改 `main.py` 路由逻辑
2. **并行混合检索** — `hybrid_retriever.py` 使用 `asyncio.gather`
3. **对话历史按需加载** — `manager.py` SQL 加 LIMIT
4. **异步任务并发控制** — `main.py` 添加 Semaphore

### 第二阶段：模型与架构优化（2-3 天）

5. **BGE-M3 模型统一加载** — `chunker.py` 创建适配器复用 GPU 模型
6. **共享数据库连接池** — 创建 `db_pool.py`，改造 4 个模块
7. **对话保存合并为批量操作** — `manager.py` 新增批量方法

### 第三阶段：吞吐量提升（3-5 天）

8. **Consumer 批量处理文档** — 改造 consumer 循环
9. **ChatController DataBuffer 优化** — 改用 `bodyToFlux(String.class)`
10. **Gateway 熔断/限流/压缩** — 配置 Spring Cloud Gateway

---

## 附录：已排除的优化项

以下为原 `performance_optimization_plan.md` 中列出但经审查后**不建议优先实施**的项：

| 原优化项 | 排除原因 |
|---------|---------|
| 数据库索引优化 | ❌ 索引已在 `migration_005_add_conversations.sql` 中创建 |
| Reranker FP16 推理 | ⚠️ 需要 GPU 支持，且当前已批量处理所有 candidates，收益有限 |
| Redis 序列化统一 | ⚠️ document-service 的 ObjectRecord 依赖 RedisTemplate 泛型，改动影响大 |
| 缓存写入去重 | ⚠️ 当前为覆盖写入，SETNX 不适用；且缓存写入频率不高，收益有限 |
| document-service 迁移 WebFlux | ⚠️ 改动过大，收益不确定 |
| 文件上传异步化 | ⚠️ 已从 TOP 10 中移除，因为当前同步方式在低并发下无明显问题 |

### 关于 Reranker 模型的说明

Reranker 模型（bge-reranker-v2-m3）的当前状态：
- ✅ 使用全局单例，**只在首次 RAG 查询时加载一次**，之后常驻内存
- ✅ 自动检测 GPU（`hybrid_retriever.py:68`）
- ✅ 已批量处理所有 candidates（一次性 tokenize 所有 pair）

**Reranker 的调用频率**：每次用户问题被路由到 RAG Agent 时，混合检索完成后都会调用 Reranker 对候选结果进行精排。

**可选的进一步优化**（非必须）：
- **FP16 推理**：如果 GPU 支持，在模型加载后添加 `model.half()`，推理速度可提升 2-3 倍
- **减少 candidates 数量**：当前全文搜索和向量检索各取 Top 50，共 100 个 candidates。如果改为各取 Top 30，Reranker 推理量减少 40%
