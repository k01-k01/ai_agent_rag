# RAG 系统性能优化方案

> 基于对项目 Python 侧和 Java 侧全部核心代码的深度分析

---

## 目录

1. [Python 侧性能瓶颈与优化](#一python-侧性能瓶颈与优化)
2. [Java 侧性能瓶颈与优化](#二java-侧性能瓶颈与优化)
3. [架构层面优化建议](#三架构层面优化建议)
4. [预期效果汇总](#四预期效果汇总)

---

## 一、Python 侧性能瓶颈与优化

### 🔴 高优先级（显著影响响应速度）

#### 1. 数据库连接池配置过低

**问题**: 四个模块各自独立创建连接池，互不共享：

| 模块 | 文件 | 配置 |
|------|------|------|
| 语义缓存 | `semantic_cache.py` | min=1, max=5 |
| 混合检索 | `hybrid_retriever.py` | min=1, max=5 |
| 对话管理 | `conversation/manager.py` | min=1, max=5 |
| 文档处理 | `consumer.py` | min=2, max=5 |

- 总共可能创建 4 个独立连接池，浪费连接资源
- 单个池 max_size=5 在高并发下成为瓶颈
- 每个池独立管理生命周期，增加维护复杂度

**优化方案**:
- 创建全局共享的数据库连接池模块 `db_pool.py`
- 将 `max_size` 提升到 20-50（根据并发需求调整）
- 所有模块统一从共享池获取连接
- 应用启动时预创建连接池，避免首次请求的冷启动延迟

#### 2. BGE-M3 模型重复加载

**问题**: 两个模块各自独立加载 BGE-M3 模型：

| 模块 | 加载方式 | 设备 |
|------|---------|------|
| `embedder.py` | `SentenceTransformer` | GPU（如果可用） |
| `chunker.py` | `HuggingFaceBgeEmbeddings` | **强制 CPU** |

- 内存浪费：两个模型实例占用双倍显存/内存（BGE-M3 约 2.2GB）
- chunker 强制 CPU 导致语义切分极慢（比 GPU 慢 10-20 倍）
- 加载两次模型增加应用启动时间

**优化方案**:
- 统一使用 `embedder.py` 中的 `SentenceTransformer` 实例
- `chunker.py` 复用同一个模型，删除独立加载逻辑
- 确保语义切分也在 GPU 上执行


#### 4. Router Agent 不必要的 LLM 调用

**问题**: 每次聊天都调用 DeepSeek API 做路由判断，即使只是简单问候

```
当前流程: 用户输入 → LLM 路由判断 (~1s) → 关键词兜底检查 → 执行
优化流程: 用户输入 → 关键词/规则前置检查 (~1ms) → LLM 路由判断（仅未命中规则时）→ 执行
```

**优化方案**:
- 将关键词检查移到 LLM 调用之前
- 添加简单问候语快速匹配（"你好"、"hi"、"hello"等直接路由到 chat）
- 添加明确的 RAG 关键词快速匹配（"根据知识库"、"文档中"等直接路由到 rag）
- 预期减少 50-70% 的 LLM 路由调用

#### 5. 异步任务未做并发控制

**问题**: `asyncio.create_task` 用于缓存写入和 Java 通知，但没有限制并发数

```python
# 当前代码 - 无限制创建任务
asyncio.create_task(semantic_cache.set_cached_answer(...))
asyncio.create_task(semantic_cache.notify_java_set_cache(...))
```

高并发下可能创建大量任务导致：
- 内存飙升
- Redis/数据库连接耗尽
- HTTP 连接池耗尽

**优化方案**:
- 使用 `asyncio.Semaphore` 限制并发任务数（建议 10-20）
- 或使用任务队列 + worker 模式

---

### 🟡 中优先级

#### 6. 混合检索串行执行

**问题**: `fulltext_search` 和 `vector_search` 串行执行，两者完全独立

```python
# 当前 - 串行，耗时 = T_fulltext + T_vector
fulltext_results = await self.fulltext_search(query, kb_id)
vector_results = await self.vector_search(query, kb_id)

# 优化 - 并行，耗时 = max(T_fulltext, T_vector)
fulltext_results, vector_results = await asyncio.gather(
    self.fulltext_search(query, kb_id),
    self.vector_search(query, kb_id)
)
```

**优化方案**: 使用 `asyncio.gather` 并行执行，可节省约 30-50% 的检索时间

#### 7. Reranker 模型推理优化

**问题**: Reranker 使用 Transformers pipeline，每次检索都重新 tokenize 和推理

**优化方案**:
- 启用 FP16 推理（如果 GPU 支持）：`model.half()`
- 增大 batch_size（当前一次处理所有 candidates）
- 考虑使用 ONNX Runtime 加速推理（可提升 2-3 倍）

#### 8. 文档处理 Consumer 单线程瓶颈

**问题**: `start_consumer` 每次只处理 1 条消息（`count=1`），处理期间无法接收新消息

**优化方案**:
- 增加 `count=5` 批量读取
- 使用 `asyncio.gather` 并发处理多个文档
- 或使用多个 consumer 实例（不同 consumer name）

#### 9. 对话历史加载全量消息

**问题**: `get_conversation` 加载所有消息，但 chat_agent 只取最近 40 条，rag_agent 只取最近 10 条

**优化方案**:
- SQL 查询时直接 `LIMIT 40`，避免传输大量无用数据
- 减少 Python 对象创建和 JSON 序列化开销

---

## 二、Java 侧性能瓶颈与优化

### 🔴 高优先级

#### 1. Redis 序列化方式不统一

**问题**: 两个服务使用不同的 Redis 客户端配置：

| 服务 | 使用方式 | 序列化 |
|------|---------|--------|
| chat-service | `StringRedisTemplate` | 手动 JSON |
| document-service | `RedisTemplate<String, Object>` | Jackson2JsonRedisSerializer |

连接到同一个 Redis 实例，但序列化方式不一致可能导致兼容性问题。

**优化方案**:
- 统一使用 `StringRedisTemplate` + 手动 JSON 序列化
- document-service 的 Redis Stream 操作改用更轻量的方式

#### 2. ChatController SSE 流 DataBuffer 处理

**问题**: 每个 DataBuffer 都创建新的 byte 数组并手动 release

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

**优化方案**:
- 使用 `response.bodyToFlux(String.class)` 替代手动 DataBuffer 处理
- 或使用 `DefaultDataBufferFactory` 复用缓冲区
- 配置 WebClient 的缓冲区大小

#### 3. Gateway 缺少负载均衡和熔断

**问题**: Gateway 直接路由到固定地址，没有容错机制

```yaml
routes:
  - id: chat-service
    uri: http://localhost:8082  # 硬编码地址
```

- 没有负载均衡（无法水平扩展）
- 没有熔断机制（下游服务故障会级联）
- 没有重试机制（临时故障导致请求失败）

**优化方案**:
- 添加 Spring Cloud LoadBalancer
- 添加 Resilience4j 熔断器
- 添加重试过滤器
- 添加超时控制

#### 4. document-service 阻塞式架构

**问题**: document-service 使用 `spring-boot-starter-web`（阻塞式 Tomcat），而 chat-service 使用 WebFlux（非阻塞）

- 混合架构下，阻塞调用可能拖累整体性能
- Tomcat 默认线程池 200，文件上传操作会长时间占用线程
- JPA 的 `@Transactional` 会持有数据库连接直到事务完成

**优化方案**:
- 考虑将 document-service 也迁移到 WebFlux + R2DBC
- 或至少为 document-service 配置独立的线程池隔离
- 使用 `@Async` 将耗时操作异步化

---

### 🟡 中优先级

#### 5. JPA show-sql 在生产环境开启

**问题**: `application.yml` 中 `show-sql: true` 在生产环境会带来额外 I/O 开销

```yaml
jpa:
  hibernate:
    ddl-auto: none
  show-sql: true  # 生产环境应关闭
```

**优化方案**:
- 使用 Profile 区分环境：开发环境开启，生产环境关闭
- 或使用 `logging.level.org.hibernate.SQL: DEBUG` 替代

#### 6. 文件上传缺少异步处理

**问题**: `uploadDocument` 是同步方法，所有操作在请求线程中完成

```
请求线程: 保存文件 → 写入数据库 → 发送 Redis Stream → 返回响应
                                          ↑ 如果 Redis 不可用，请求会阻塞
```

**优化方案**:
- 使用 `@Async` 将 Redis Stream 发送异步化
- 配置专门的线程池处理上传任务
- 先返回响应，再异步处理

#### 7. Redis Stream 消息序列化开销

**问题**: `sendProcessingMessage` 使用 `ObjectRecord` + Jackson 序列化

**优化方案**:
- 使用 `MapRecord` 或手动构建 `Record` 减少序列化开销
- 预序列化消息体为 JSON 字符串

#### 8. 缓存写入缺少去重机制

**问题**: 同一个问题短时间内多次命中，可能触发多次缓存写入

**优化方案**:
- 使用 Redis SETNX 或分布式锁确保缓存只写一次
- 或使用布隆过滤器做第一层去重

---

## 三、架构层面优化建议

### 🔴 高优先级


#### 2. 对话保存的冗余操作

**问题**: 每次聊天响应完成后，`add_message` 被调用两次（user + assistant），每次都是独立事务

```python
# 两次独立数据库操作
await conv_manager.add_message(conversation_id, "user", message)
await conv_manager.add_message(conversation_id, "assistant", full_response, ...)
```

**优化方案**:
- 使用批量插入：一次 INSERT 两条消息
- 将 `updated_at` 更新合并到同一个事务中
- 减少 50% 的数据库往返次数

#### 3. Gateway 单点瓶颈

**问题**: 所有前端请求都经过 Gateway，但 Gateway 没有配置优化

**优化方案**:
- 添加响应压缩（Gzip）
- 添加请求限流（RequestRateLimiter）
- 添加静态资源缓存策略
- 配置 WebFlux 线程池

---

### 🟡 中优先级

#### 4. 连接池与线程池配置

**问题**: 所有服务都使用默认线程池/连接池配置

**优化建议**:

| 组件 | 当前配置 | 建议配置 |
|------|---------|---------|
| uvicorn workers | 默认（1） | CPU 核心数 × 2 + 1 |
| asyncpg 连接池 | min=1, max=5 | min=5, max=50 |
| Redis 连接池 | 默认 | max=50 |
| Tomcat 线程池 | 默认 200 | 根据并发调整 |
| JDBC 连接池 | 默认 10 | min=5, max=30 |

#### 5. 数据库索引优化

**问题**: 当前只有 chunks 表有索引，conversations 和 conversation_messages 表缺少索引

**建议添加的索引**:

```sql
-- conversations 表
CREATE INDEX idx_conversations_updated_at ON conversations(updated_at DESC);

-- conversation_messages 表
CREATE INDEX idx_messages_conversation_created 
ON conversation_messages(conversation_id, created_at ASC);
```

---

## 四、预期效果汇总

| 优先级 | 优化项 | 所属侧 | 预期提升 | 实施难度 |
|--------|--------|--------|---------|---------|
| 🔴 | 共享数据库连接池 | Python | 减少 60% 连接创建开销 | ⭐ |
| 🔴 | 模型统一加载 | Python | 节省 50% 显存/内存 | ⭐⭐ |
| 🔴 | 缓存流式输出加速 | Python | 缓存命中时响应速度提升 3-5x | ⭐ |
| 🔴 | Router 关键词前置 | Python | 减少 50-70% 的 LLM 路由调用 | ⭐ |
| 🔴 | 异步任务并发控制 | Python | 避免高并发下内存飙升 | ⭐ |
| 🔴 | 两级缓存去 HTTP 化 | 架构 | 减少 100-200ms 延迟 | ⭐⭐⭐ |
| 🔴 | Gateway 熔断/限流 | Java | 提升系统稳定性 | ⭐⭐ |
| 🟡 | 并行混合检索 | Python | 检索速度提升 30-50% | ⭐ |
| 🟡 | Reranker FP16 推理 | Python | 推理速度提升 2-3x | ⭐⭐ |
| 🟡 | Consumer 批量处理 | Python | 文档处理吞吐量提升 3-5x | ⭐⭐ |
| 🟡 | 对话历史按需加载 | Python | 减少 60% 数据传输 | ⭐ |
| 🟡 | 异步文件上传 | Java | 上传响应速度提升 2-3x | ⭐⭐ |
| 🟡 | 数据库索引优化 | 架构 | 查询速度提升 5-10x | ⭐ |

### 优先级说明

- **🔴 高优先级**: 直接影响用户体验或系统稳定性，建议优先实施
- **🟡 中优先级**: 提升系统效率或可维护性，可在高优先级完成后实施
- **难度**: ⭐（简单） ⭐⭐（中等） ⭐⭐⭐（复杂）

### 快速见效的 TOP 5 优化

如果时间有限，建议优先实施以下 5 项优化，投入产出比最高：

1. **Router 关键词前置**（Python, ⭐）— 修改 `main.py` 中的路由逻辑，将关键词检查移到 LLM 调用之前
2. **缓存流式输出加速**（Python, ⭐）— 调整 `CHUNK_SIZE` 和延迟参数
3. **并行混合检索**（Python, ⭐）— 使用 `asyncio.gather` 并行执行全文搜索和向量检索
4. **共享数据库连接池**（Python, ⭐）— 创建全局 `db_pool.py` 模块
5. **对话历史按需加载**（Python, ⭐）— SQL 查询添加 `LIMIT` 子句
