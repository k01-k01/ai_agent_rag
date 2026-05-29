# 个人RAG知识库 - Issue 拆分计划

## Issue 1: 项目脚手架与基础设施搭建

### 目标
搭建前后端基础项目结构，配置好 Docker Compose 环境，确保各服务能独立运行。

### 具体任务
1. **前端脚手架**
   - 初始化 React + Vite + TypeScript + Tailwind 项目
   - 配置项目基础路由（知识库管理页、聊天页）
   - 配置基础 API 请求工具（Axios）

2. **Java 后端脚手架**
   - jdk17
   - 搭建 Spring Cloud Gateway 微服务（端口 8080）
   - 搭建 document-service 微服务（端口 8081）
   - 搭建 chat-service 微服务（端口 8082）
   - 配置服务间通信

3. **Python 后端脚手架**
   - 搭建 FastAPI 项目结构
   - 配置 LangChain + LangGraph 环境
   - 基础 API 接口定义

4. **Docker Compose 环境**
   - PostgreSQL + pgvector（端口 5432），密码 `2wsxcde3`
   - Redis（端口 6379，同时作为缓存和消息队列）
   - 各服务的 Dockerfile

5. **模型路径配置**
   - LLM: 使用 DeepSeek API（deepseek-chat 模型）
   - BGE-M3: `D:\project\models\bge-m3`
   - Reranker: `D:\project\models\bge-reranker-v2-m3`

6. **数据库初始化**
   - 创建 `init.sql`
   - `knowledge_bases` 表（id, name, created_at）
   - `documents` 表（id, knowledge_base_id, name, status, created_at）
   - `chunks` 表（id, knowledge_base_id, document_id, content, metadata, embedding, search_vector）
   - 安装 pgvector 扩展
   - 创建 TSVECTOR 全文索引（GIN）和 HNSW 向量索引

### 验收标准
- [ ] 前端 `npm run dev` 启动成功，访问 http://localhost:5173
- [ ] Java 三个微服务启动成功
- [ ] Python FastAPI 启动成功，访问 http://localhost:8000/docs
- [ ] Docker Compose 所有容器启动成功
- [ ] PostgreSQL 连接成功，数据库表创建成功

---

## Issue 2: 知识库管理功能

### 目标
实现知识库的创建、删除、列表查询，以及文档的上传与存储。

### 具体任务
1. **前端 - 知识库管理页**
   - 知识库列表展示
   - 创建知识库按钮 + 弹窗
   - 删除知识库功能

2. **Java document-service**
   - 创建知识库 API
   - 删除知识库 API
   - 获取知识库列表 API
   - 文档上传 API（支持 txt、md、pdf、word）
   - 文档存储到本地文件系统

3. **Python 文档处理模块**
   - Redis Streams 消费者监听
   - 接收文档处理任务
   - 解析不同格式文档

4. **数据库**
   - `documents` 表的 status 字段更新逻辑

### 验收标准
- [ ] 能在前端创建/删除知识库
- [ ] 能上传文档到指定知识库
- [ ] 文档上传后在数据库中有记录
- [ ] Redis Streams 消息能正确传递到 Python

---

## Issue 3: 文档处理与索引构建

### 目标
实现文档的切分、向量化、索引构建完整流程。

### 具体任务
1. **文本切分模块**
   - 使用 LangChain 按语义切分（Semantic Chunking）
   - 存入 PostgreSQL chunks 表

2. **全文搜索索引构建**
   - 使用 PostgreSQL 内置的 TSVECTOR 和 GIN 索引
   - 在 chunks.search_vector 上创建全文检索索引

3. **向量索引构建**
   - 使用 BGE-M3 模型生成 1024 维向量
   - 存入 pgvector embedding 列
   - 创建 HNSW 索引

4. **文档处理状态更新**
   - Python 处理完成后通知 Java
   - 更新 documents 表 status 字段

### 验收标准
- [ ] 上传文档后能在 30 秒内完成处理
- [ ] chunks 表有正确的切分数据
- [ ] TSVECTOR 全文索引创建成功，能进行关键词检索
- [ ] HNSW 索引创建成功，能进行向量检索
- [ ] 处理失败时状态为 error，支持手动重试

---

## Issue 4: 聊天界面与流式输出

### 目标
实现前端聊天界面，支持多轮对话和流式输出。

### 具体任务
1. **前端聊天页**
   - 知识库选择下拉框
   - 聊天消息列表（用户/AI 消息区分）
   - 消息输入框 + 发送按钮
   - SSE 流式输出显示
   - Agent 类型标识 UI（显示当前调用的是 rag agent / chat agent）
   - 检索来源展示区域（预留，RAG 回答时能看到引用自哪个文档）

2. **Java chat-service**
   - SSE 接口实现（透传 Python FastAPI 的 SSE 流式响应）
   - 缓存命中时模拟流式输出（拆分为小块依次发送，见 Issue 7）

3. **Python SSE 接口**
   - FastAPI SSE 流式响应基础框架

### 验收标准
- [ ] 前端能选择知识库并发送消息
- [ ] 消息能流式显示在前端
- [ ] 支持多轮对话上下文延续
- [ ] Python SSE 接口能正常返回流式响应

---

## Issue 5: Router Agent 路由功能

### 目标
实现 Router Agent，使用 LLM 语义理解判断问题类型。

### 具体任务
1. **Router Agent 实现**
   - 基于 LangChain 框架
   - 设计路由 prompt（判断是否需要检索知识库）
   - 返回路由结果（rag/chat）

2. **LangGraph 工作流**
   - 配置 Router 节点
   - 配置条件路由边

3. **边界情况处理**
   - 模糊问题默认路由逻辑
   - 路由失败处理

### 验收标准
- [ ] 普通对话（如"今天天气怎么样"）路由到 Chat Agent
- [ ] 涉及知识库的问题（如"文档里写了什么"）路由到 RAG Agent
- [ ] 前端能正确显示使用的 Agent 类型
- [ ] SSE 流式输出时，前端显示调用的是哪个 Agent（rag/chat）

---

## Issue 6: Chat Agent 纯对话功能

### 目标
实现 Chat Agent 的纯对话功能，不涉及知识库检索。

### 具体任务
1. **Chat Agent 实现**
   - 基于 LangChain 框架
   - 直接调用 DeepSeek API
   - 支持多轮对话历史

2. **对话历史管理**
   - 存储用户对话历史
   - 支持上下文延续

### 验收标准
- [ ] Chat Agent 能进行流畅的闲聊
- [ ] 多轮对话中能记住之前的内容
- [ ] 流式输出回答

---

## Issue 7: 两级缓存机制

### 目标
实现对于用户提问的 Java 侧一级缓存（Redis 哈希匹配）和 Python 侧二级缓存（语义匹配）。

### 具体任务
1. **一级缓存（Redis）**
   - 问题规范化（统一小写、去除标点）
   - MD5 哈希作为 key
   - 完整答案作为 value
   - TTL 24 小时
   - 缓存命中时模拟流式输出

2. **二级缓存（pgvector）**
   - BGE-M3 向量化用户问题
   - 在 pgvector 中检索相似历史问题
   - 相似度 > 0.85 命中
   - 命中时，按每5字符拆分答案小块，流式返回缓存答案

3. **缓存写入**
   - RAG/Chat Agent 生成答案后
   - 异步写入 Redis（一级缓存）
   - 异步写入 pgvector（二级缓存）

### 验收标准
- [ ] 相同问题第二次提问能从 Redis 快速返回
- [ ] 相似问题（语义相近）能从 pgvector 返回
- [ ] 流式输出体验一致
- [ ] 缓存数据 24 小时后自动过期
- [ ] 一级缓存命中时，将答案按每5字符拆分为小块依次发送，模拟流式输出

---

## Issue 8: RAG Agent 混合检索

### 目标
实现 RAG Agent 的混合检索流程：全文搜索 + 向量检索 + RRF 融合 + Reranker。

### 具体任务
1. **全文搜索检索**
   - 查询 chunks 表的 TSVECTOR 全文索引
   - 返回 Top 50 结果

2. **向量检索**
   - 用户问题向量化（BGE-M3）
   - 查询 pgvector HNSW 索引
   - 返回 Top 50 结果

3. **RRF 融合**
   - 使用标准 RRF 公式（k=60）
   - 融合全文搜索和向量检索结果

4. **Reranker 精排**
   - 使用 bge-reranker-v2-m3 模型
   - 返回 Top 5 结果

5. **LLM 回答**
   - 将 Top 5 chunk 作为上下文
   - 调用 DeepSeek API 生成回答
   - 流式输出回答

### 验收标准
- [ ] 检索结果包含来自多个知识库的文档片段
- [ ] Reranker 精排后结果质量提升
- [ ] 流式输出回答流畅
- [ ] 回答中显示检索来源（文档标题 + chunk 内容摘要），前端能直观看到引用自哪个文档

---

## Issue 9: 整体联调与优化

### 目标
确保所有模块联调正常，优化性能和用户体验。

### 具体任务
1. **完整流程联调**
   - 上传文档 → 处理 → 检索 → 回答
   - 验证整体流程

2. **性能优化**
   - 文档处理异步化
   - 缓存命中率优化

3. **错误处理完善**
   - 服务不可用时降级处理
   - 友好的错误提示

4. **文档整理**
   - README 编写
   - API 文档完善

### 验收标准
- [ ] 完整功能可正常运行
- [ ] 文档处理 < 30 秒
- [ ] 聊天响应流畅
- [ ] 有完整的运行文档
