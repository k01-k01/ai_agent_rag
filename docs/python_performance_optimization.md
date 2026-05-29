# Python 侧性能优化方案

> 基于对 Python 侧全部核心代码的深度审查
>
> 审查日期：2026-05-28

---

## 目录

1. [优化项总览](#一优化项总览)
2. [各优化项详细说明](#二各优化项详细说明)
3. [实施路线图](#三实施路线图)

---

## 一、优化项总览

| 序号 | 优化项 | 所属模块 | 优先级 | 预期提升 | 改动量 | 风险 |
|:----:|--------|:--------:|:------:|---------|:------:|:----:|
| 1 | **信号量限流失效修复** | main.py | 🔴 | 修复缓存写入并发控制失效问题 | ⭐ 极小 | 低 |
| 2 | **零向量引用 Bug 修复** | embedder.py | 🔴 | 修复批量嵌入结果数据错误 | ⭐ 极小 | 低 |
| 3 | **chunks 批量插入** | consumer.py | 🟡 | 大文档处理性能提升 10x+ | ⭐ 极小 | 低 |
| 4 | **httpx 客户端复用** | semantic_cache.py | 🟡 | 减少 HTTP 连接建立开销 | ⭐ 极小 | 低 |
| 5 | **模拟流式延迟过大** | semantic_cache.py | 🟢 | 长答案输出速度提升 8x | ⭐ 极小 | 低 |
| 6 | **连接池参数调优** | db_pool.py | 🟢 | 提升并发处理能力 | ⭐ 极小 | 低 |
| 7 | **历史消息插入位置** | rag_agent.py | 🟢 | 优化 RAG 提示词结构 | ⭐ 极小 | 低 |

### 优先级说明

- **🔴 高优先级**: 存在 Bug 或功能失效，建议优先修复
- **🟡 中优先级**: 提升性能或效率，可在高优先级完成后实施
- **🟢 低优先级**: 微优化或最佳实践，可在迭代中逐步完善
- **改动量**: ⭐（极小，<10行）

---

## 二、各优化项详细说明

---

### 🥇 TOP 1：信号量限流失效修复

#### 问题描述

`main.py` 中 `_cache_semaphore` 用于控制缓存写入的并发数，但当前使用方式**没有实际限流效果**：

```python
# 当前代码 - 信号量无效
async with _cache_semaphore:          # 获取信号量
    asyncio.create_task(              # create_task 立即返回，信号量马上释放
        semantic_cache.set_cached_answer(...)
    )
async with _cache_semaphore:          # 同上
    asyncio.create_task(
        semantic_cache.notify_java_set_cache(...)
    )
```

`asyncio.create_task()` 是**非阻塞**的，它立即返回一个 Task 对象，不会等待协程执行完毕。因此 `async with _cache_semaphore` 在 `create_task` 返回后立即释放信号量，后续的并发任务数量不受限制。

同理，第 195-203 行缓存命中时的 `notify_java_set_cache` 也存在同样问题。

#### 涉及文件

- `python/main.py`（第 195-203 行、第 299-306 行）

#### 优化方案

新增一个带信号量的包装函数，将信号量放在任务**内部**使用：

```python
async def _cached_write_with_semaphore(coro):
    """在信号量控制下执行缓存写入协程"""
    async with _cache_semaphore:
        await coro
```

调用处改为：

```python
# 缓存未命中时写入两级缓存
if full_response:
    rag_sources = sources_data if route_type == "rag" else None
    asyncio.create_task(
        _cached_write_with_semaphore(
            semantic_cache.set_cached_answer(message, full_response, route_type, rag_sources)
        )
    )
    asyncio.create_task(
        _cached_write_with_semaphore(
            semantic_cache.notify_java_set_cache(message, full_response, route_type, rag_sources)
        )
    )
```

缓存命中时同理：

```python
# 缓存命中时通知 Java 写入 L1 缓存
asyncio.create_task(
    _cached_write_with_semaphore(
        semantic_cache.notify_java_set_cache(
            message,
            cached_entry["answer"],
            cached_agent_type,
            cached_sources,
        )
    )
)
```

#### 预期效果

- 信号量真正限制并发数，保护 Python 端和 Java 端不被突发缓存写入请求打满
- 超出限制时任务排队等待，不会丢弃

---

### 🥇 TOP 2：零向量引用 Bug 修复

#### 问题描述

`embedder.py` 的 `generate_embeddings_batch` 方法中，初始化结果列表时使用了**列表乘法**，导致所有元素指向同一个列表对象：

```python
# 当前代码 - 所有元素指向同一个列表对象
result = [[0.0] * 1024] * len(texts)
```

`[0.0] * 1024` 创建了一个包含 1024 个浮点数的列表。然后 `[result] * n` 创建了 n 个**指向同一对象**的引用。当后续代码修改 `result[idx] = emb.tolist()` 时，实际上所有元素都会被最后一次赋值覆盖。

#### 涉及文件

- `python/modules/document_processor/embedder.py`（第 96 行）

#### 优化方案

使用列表推导式创建独立对象：

```python
result = [[0.0] * 1024 for _ in range(len(texts))]
```

#### 预期效果

- 每个元素都是独立的列表对象
- 批量嵌入结果正确映射回原始顺序

---

### 🥇 TOP 3：chunks 批量插入

#### 问题描述

`consumer.py` 的文档处理流程中，chunks 是**逐条 INSERT** 的：

```python
# 当前代码 - 逐条插入
for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
    await conn.execute(
        """
        INSERT INTO chunks (knowledge_base_id, document_id, content, metadata, embedding)
        VALUES ($1, $2, $3, $4::jsonb, $5::vector)
        """,
        kb_id, doc_id, chunk["content"], chunk_meta, embedding_str,
    )
```

对于大文档（如 500 页 PDF 可能产生上千个 chunks），每次 INSERT 都是一次网络往返，性能极差。

#### 涉及文件

- `python/modules/document_processor/consumer.py`（第 96-110 行）

#### 优化方案

使用 `asyncpg` 的 `executemany()` 批量插入：

```python
# 准备批量数据
chunk_records = []
for chunk, embedding in zip(chunks, embeddings):
    chunk_meta = json.dumps(chunk["metadata"], ensure_ascii=False)
    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
    chunk_records.append((kb_id, doc_id, chunk["content"], chunk_meta, embedding_str))

# 批量插入（单次网络往返）
await conn.executemany(
    """
    INSERT INTO chunks (knowledge_base_id, document_id, content, metadata, embedding)
    VALUES ($1, $2, $3, $4::jsonb, $5::vector)
    """,
    chunk_records,
)
```

#### 预期效果

- 将 N 次网络往返减少为 1 次
- 大文档处理性能提升 10 倍以上

---

### 🥇 TOP 4：httpx 客户端复用

#### 问题描述

`semantic_cache.py` 的 `notify_java_set_cache` 方法每次调用都创建和销毁 httpx 客户端：

```python
# 当前代码 - 每次创建新客户端
async with httpx.AsyncClient(timeout=5.0) as client:
    response = await client.post(url, json=payload)
```

每次创建客户端都会建立新的 TCP 连接，包括 DNS 解析和 TLS 握手（如果使用 HTTPS），在高并发下开销明显。

#### 涉及文件

- `python/modules/cache/semantic_cache.py`（第 177 行）

#### 优化方案

将 httpx 客户端提升为类级别的延迟初始化单例：

```python
class SemanticCache:
    def __init__(self):
        self.similarity_threshold = CACHE_SIMILARITY_THRESHOLD
        self._http_client = None  # 延迟初始化

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建复用的 httpx 客户端"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=5.0)
        return self._http_client

    async def notify_java_set_cache(self, ...):
        # ... 原有代码 ...
        client = await self._get_http_client()
        response = await client.post(url, json=payload)
        # 注意：不再使用 async with，客户端持续复用
```

如果需要更彻底的复用，也可以使用模块级单例：

```python
# 模块级全局 httpx 客户端
_http_client: Optional[httpx.AsyncClient] = None

async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=5.0)
    return _http_client
```

#### 预期效果

- 复用 TCP 连接，减少连接建立开销
- 高并发下延迟降低

---

### 🥇 TOP 5：模拟流式延迟过大

#### 问题描述

`semantic_cache.py` 的 `simulate_stream_from_cache` 方法模拟流式输出时，每 5 字符延迟 50ms：

```python
CHUNK_SIZE = 5       # 每块 5 字符
# ...
await asyncio.sleep(0.05)  # 每块延迟 50ms
```

对于 2000 字的答案，需要输出 400 个块，总耗时 400 × 0.05 = **20 秒**，用户体验较差。

#### 涉及文件

- `python/modules/cache/semantic_cache.py`（第 25 行、第 226 行）

#### 优化方案

增大块大小并减小延迟：

```python
CHUNK_SIZE = 20       # 从 5 改为 20
STREAM_DELAY = 0.02   # 从 0.05 改为 0.02
```

同时将延迟常量提取为模块级变量，便于后续调整：

```python
# 模拟流式输出参数
CHUNK_SIZE = 20       # 每块字符数
STREAM_DELAY = 0.02   # 块间延迟（秒）
```

#### 预期效果

- 2000 字答案输出时间从 20 秒缩短到约 2 秒
- 前端用户体验显著提升

---

### 🥇 TOP 6：连接池参数调优

#### 问题描述

`db_pool.py` 中数据库连接池参数偏保守：

```python
_pool = await asyncpg.create_pool(
    dsn,
    min_size=2,        # 最小连接数
    max_size=20,       # 最大连接数
    command_timeout=30, # 命令超时
)
```

- `min_size=2`：启动时只有 2 个连接，突发流量下需要动态创建新连接，增加延迟
- `max_size=20`：文档处理时可能需要同时处理多个文档（每个文档涉及解析、切分、向量化、插入），20 个连接可能不够
- `command_timeout=30`：文档处理（特别是向量化 + 插入）可能超过 30 秒

#### 涉及文件

- `python/db_pool.py`（第 23-28 行）

#### 优化方案

根据实际场景调整参数：

```python
_pool = await asyncpg.create_pool(
    dsn,
    min_size=5,        # 从 2 改为 5，保持更多就绪连接
    max_size=50,       # 从 20 改为 50，支持更高并发
    command_timeout=60, # 从 30 改为 60，文档处理可能耗时较长
)
```

#### 预期效果

- 减少突发流量下的连接创建延迟
- 支持更高的并发处理能力

---

### 🥇 TOP 7：历史消息插入位置

#### 问题描述

`rag_agent.py` 的 `stream_chat` 方法中，历史消息插入到 `messages[-1]`（user 消息）之前：

```python
messages = self._build_rag_prompt(message, context)
# messages = [system_prompt, user_message]

if history:
    for hist_msg in history[-10:]:
        if hist_msg["role"] in ("user", "assistant"):
            messages.insert(
                -1,  # 在最后一条 user 消息之前插入
                {"role": hist_msg["role"], "content": hist_msg["content"]},
            )
```

这导致消息顺序变为：`[system, hist1, hist2, ..., user]`。虽然功能上正确，但 system prompt 和 user 消息之间插入大量历史消息，可能稀释 system prompt 对模型的影响。

#### 涉及文件

- `python/modules/agents/rag_agent.py`（第 124-128 行）

#### 优化方案

将历史消息插入到 system prompt 之后（index 1），保持 system prompt 紧邻对话上下文：

```python
if history:
    for hist_msg in history[-10:]:
        if hist_msg["role"] in ("user", "assistant"):
            messages.insert(
                1,  # 插入到 system 之后（index 1）
                {"role": hist_msg["role"], "content": hist_msg["content"]},
            )
```

消息顺序变为：`[system, hist1, hist2, ..., user]`，与之前相同，但插入位置更明确。

> 注：当前实现功能上正确，此优化主要是代码可读性和维护性改进。

#### 预期效果

- 代码意图更清晰
- 保持 system prompt 与对话上下文的合理位置关系

---

## 三、实施路线图

### 第一阶段：Bug 修复（0.5 天）

| 优化项 | 改动量 | 说明 |
|--------|:------:|------|
| TOP 1：信号量限流失效修复 | ~10 行 | 新增包装函数 + 修改调用处 |
| TOP 2：零向量引用 Bug 修复 | 1 行 | 列表推导式替代列表乘法 |

### 第二阶段：性能提升（0.5 天）

| 优化项 | 改动量 | 说明 |
|--------|:------:|------|
| TOP 3：chunks 批量插入 | ~10 行 | executemany 替代逐条 INSERT |
| TOP 4：httpx 客户端复用 | ~10 行 | 延迟初始化单例客户端 |

### 第三阶段：微优化（0.5 天）

| 优化项 | 改动量 | 说明 |
|--------|:------:|------|
| TOP 5：模拟流式延迟过大 | 2 行 | 调整 CHUNK_SIZE 和 STREAM_DELAY |
| TOP 6：连接池参数调优 | 3 行 | 调整 min_size/max_size/command_timeout |
| TOP 7：历史消息插入位置 | 1 行 | 修改 insert index |

---

## 附录：已排除的优化项

以下为审查中考虑过但**不建议优先实施**的项：

| 优化项 | 排除原因 |
|--------|---------|
| 全文搜索/向量搜索 SQL 去重 | 当前代码可读性较好，去重后反而增加复杂度 |
| 添加 Redis 连接池 | 当前 Redis 使用频率不高（仅 consumer 使用），单连接足够 |
| 使用 orjson 替代 json | 收益有限，且增加依赖 |
| 添加请求缓存（如路由判断结果缓存） | 路由判断本身很快（<1s），缓存收益不大 |
