# issue1 
## Issue 1: 项目脚手架与基础设施搭建 - 实施计划

我已经详细阅读了 `docs/project.md` 和 `docs/issue.md`，以下是针对 Issue 1 的完整实施计划。

### 项目整体结构

```
demo1/
├── frontend/                        # React + Vite + TS + Tailwind
│   ├── src/
│   │   ├── pages/
│   │   │   ├── KnowledgeBase.tsx     # 知识库管理页 (占位)
│   │   │   └── Chat.tsx              # 聊天页 (占位)
│   │   ├── api/
│   │   │   └── index.ts             # Axios 基础请求封装
│   │   ├── App.tsx                   # 路由配置
│   │   ├── main.tsx
│   │   └── index.css                 # Tailwind 入口
│   ├── index.html
│   ├── tailwind.config.js
│   ├── postcss.config.js
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── package.json
│
├── java/
│   ├── gateway/                      # Spring Cloud Gateway (端口 8080)
│   │   ├── pom.xml
│   │   └── src/main/java/com/rag/gateway/
│   │       └── GatewayApplication.java
│   │   └── src/main/resources/
│   │       └── application.yml
│   │
│   ├── document-service/             # Document Service (端口 8081)
│   │   ├── pom.xml
│   │   └── src/main/java/com/rag/document/
│   │       ├── DocumentServiceApplication.java
│   │       └── controller/
│   │           └── DocumentController.java   # 基础占位API
│   │   └── src/main/resources/
│   │       └── application.yml
│   │
│   └── chat-service/                 # Chat Service (端口 8082)
│       ├── pom.xml
│       └── src/main/java/com/rag/chat/
│           ├── ChatServiceApplication.java
│           └── controller/
│               └── ChatController.java      # 基础占位API
│       └── src/main/resources/
│           └── application.yml
│
├── python/                           # FastAPI 服务 (端口 8000)
│   ├── main.py                       # FastAPI 入口
│   ├── config.py                     # 模型路径等配置
│   └── requirements.txt
│
├── docker/
│   └── docker-compose.yml            # PostgreSQL + Redis
│
├── db/
│   └── init.sql                      # 数据库初始化脚本
│
└── .env                              # 环境变量
```

### 实施步骤

#### 步骤 1：创建目录结构
创建上述所有目录。

#### 步骤 2：Docker Compose 环境 (`docker/docker-compose.yml`)
- **PostgreSQL 15 + pgvector + pg_bm25**：端口 5432，用户名 `postgres`，密码 `2wsxcde3`
- **Redis 7**：端口 6379，同时作为缓存和消息队列
- 挂载 `db/init.sql` 到 `/docker-entrypoint-initdb.d/` 自动初始化

#### 步骤 3：数据库初始化脚本 (`db/init.sql`)
- 安装扩展：`CREATE EXTENSION vector;` 和 `CREATE EXTENSION pg_bm25;`
- 创建三张表：
  - `knowledge_bases` (id UUID PK, name VARCHAR, created_at TIMESTAMP)
  - `documents` (id UUID PK, knowledge_base_id UUID FK, name VARCHAR, status VARCHAR, created_at TIMESTAMP)
  - `chunks` (id UUID PK, knowledge_base_id UUID, document_id UUID FK, content TEXT, metadata JSONB, embedding vector(1024))
- 创建 HNSW 索引（embedding 列）和 BM25 索引（content 列）

#### 步骤 4：前端脚手架 (`frontend/`)
- 使用 Vite 创建 React + TypeScript 项目
- 配置 Tailwind CSS v3（postcss.config.js、tailwind.config.js）
- 安装 `react-router-dom`、`axios`
- 创建两个占位页面组件：
  - `KnowledgeBase.tsx` — 知识库管理页面
  - `Chat.tsx` — 聊天页面
- `App.tsx` 中配置路由（`/` → 知识库管理，`/chat` → 聊天页）
- `api/index.ts` 封装 Axios：baseURL、请求/响应拦截器

#### 步骤 5：Java 后端脚手架 (`java/`)
三个独立的 Spring Boot 3.2 项目（JDK 17），统一使用 Maven 父子模块管理：

- **gateway** (端口 8080)：
  - Spring Cloud Gateway + Spring Boot
  - `application.yml` 配置路由规则：`/api/documents/**` → `localhost:8081`，`/api/chat/**` → `localhost:8082`
  - 启用 CORS
  
- **document-service** (端口 8081)：
  - Spring Boot + Spring Data JPA + PostgreSQL
  - 占位 Controller：`GET /api/documents/health`
  
- **chat-service** (端口 8082)：
  - Spring Boot + Spring WebFlux
  - 占位 Controller：`GET /api/chat/health`
  - 引入 Redis 依赖（为后续缓存做准备）

#### 步骤 6：Python 后端脚手架 (`python/`)
- `main.py`：FastAPI 应用入口
  - `GET /health` 健康检查
  - `GET /api/chat/stream` SSE 流式接口占位
- `config.py`：模型路径配置
  - `LLM_MODEL_PATH = "D:\\project\\models\\qwen\\Qwen2___5-1___5B-Instruct"`
  - `BGE_M3_PATH = "D:\\project\\models\\bge-m3"`
  - `RERANKER_PATH = "D:\\project\\models\\bge-reranker-v2-m3"`
- `requirements.txt`：fastapi、uvicorn、langchain、langgraph、psycopg2、sentence-transformers、redis、pymupdf、python-docx 等

#### 步骤 7：环境变量配置文件 (`.env`)
集中管理所有服务的环境变量：数据库连接、Redis 连接、模型路径、端口配置等。

### 验收标准对照

| 验收项 | 实现方案 |
|------|---------|
| 前端 `npm run dev` 启动成功 | Vite + React + TS + Tailwind 项目可运行 |
| Java 三个微服务启动成功 | 三个独立 Spring Boot 可各自启动 |
| Python FastAPI 启动成功 | `uvicorn main:app --port 8000` 可运行，`/docs` 可见 Swagger |
| Docker Compose 容器启动成功 | PostgreSQL + Redis 容器正常运行 |
| PostgreSQL 连接成功，表创建成功 | `init.sql` 自动建表，`\dt` 可看到三张表 |

---





# issue2
## 实现方案（5个模块）

### 模块 1：Java document-service — 数据层 (Model + Repository + DTO)

__新建文件：__

- `model/KnowledgeBase.java` — JPA 实体：id, name, createdAt
- `model/Document.java` — JPA 实体：id, kbId, name, status, filePath, fileType, fileSize, createdAt
- `repository/KnowledgeBaseRepository.java` — JPA Repository
- `repository/DocumentRepository.java` — JPA Repository（含 `findByKnowledgeBaseId` 查询）
- `dto/CreateKnowledgeBaseRequest.java` — 创建 KB 的 DTO
- `dto/DocumentUploadResponse.java` — 上传文档响应 DTO（无全局异常处理器时用简单 POJO）

### 模块 2：Java document-service — 业务与接口层 (Service + Controller)

__新建文件：__

- `service/KnowledgeBaseService.java` — 知识库 CRUD 业务逻辑

- `service/DocumentService.java` — 文档上传 + 存储 + Redis Stream 通知

- `service/FileStorageService.java` — 本地文件系统存储（上传目录 `./uploads/{kbId}/`）

- `controller/KnowledgeBaseController.java` — REST API：

  - `GET /api/documents/knowledge-bases` — 列表
  - `POST /api/documents/knowledge-bases` — 创建
  - `DELETE /api/documents/knowledge-bases/{id}` — 删除（级联删除关联文档和文件）

- `controller/DocumentController.java` — REST API：

  - `POST /api/documents/knowledge-bases/{kbId}/documents` — 上传文件（multipart）
  - `GET /api/documents/knowledge-bases/{kbId}/documents` — 文档列表

__pom.xml 补充依赖：__ 添加 `spring-boot-starter-data-redis`（用于 Redis Streams 发送文档处理消息）

### 模块 3：前端 — 知识库管理页完整实现

__修改文件：__

- `pages/KnowledgeBase.tsx` — 完整重写：

  - 知识库列表（卡片形式，显示名称、文档数量、创建时间）
  - 「创建知识库」按钮 → 弹出 Modal（输入名称，调用 POST API）
  - 每个知识库卡片：删除按钮（确认弹窗，调用 DELETE API）、上传文档按钮
  - 文档列表展示（点击知识库展开，显示文档名、状态、上传时间）
  - 文档上传（拖拽/点击上传，支持 txt/md/pdf/word）

- `api/index.ts` — 新增 4 个 API 函数：

  - `getKnowledgeBases()` — GET
  - `createKnowledgeBase(name)` — POST
  - `deleteKnowledgeBase(id)` — DELETE
  - `uploadDocument(kbId, file)` — POST (multipart/form-data)
  - `getDocuments(kbId)` — GET

__需要添加的依赖：__ 无（现有依赖已足够，使用原生 fetch 或 axios）

### 模块 4：Python — 文档处理消费者

__新建/修改文件：__

- `modules/document_processor/consumer.py` — Redis Streams 消费者：

  - 监听 stream `documents:processing`
  - 消费组 `document-processor-group`
  - 收到消息后更新 DB 中文档状态为 `PROCESSING`
  - 根据文件类型（txt/md/pdf/word）调用对应解析器
  - 解析成功 → 状态 `COMPLETED`，解析失败 → 状态 `ERROR`
  - 预留 Issue 3 的 chunk 切分、向量化入口

- `modules/document_processor/parser.py` — 文档解析器：

  - `TextParser` — 直接读取 txt/md
  - `PdfParser` — 使用 PyPDF2 或 pdfplumber
  - `WordParser` — 使用 python-docx

- `python/main.py` — 启动时注册消费者后台任务（`@app.on_event("startup")`）

- `python/requirements.txt` — 补充：PyPDF2, python-docx, redis（如未包含）

### 模块 5：Gateway 路由确认

- 无需修改，Issue 1 已配置好 `/api/documents/**` → `http://localhost:8081`

---

## 数据流

```javascript
前端 → [POST /api/documents/knowledge-bases/{id}/documents]
     → Gateway (:8080) 路由
     → document-service (:8081) 
        1. 保存文件到本地 uploads/{kbId}/{uuid}-{filename}
        2. 写入 documents 表 (status=PENDING)
        3. 发送 Redis Stream 消息到 documents:processing
     → Python consumer 监听
        1. 读取消息 → 更新 status=PROCESSING
        2. 解析文档内容（txt/md/pdf/word）
        3. Issue 3 将扩展：chunk + embedding + 更新 status=COMPLETED
```

---

## 状态流转

```javascript
PENDING → (Python consumer 消费) → PROCESSING → COMPLETED / ERROR
```

---

## 建议实施顺序

1. __Java Model + Repository + DTO__ — 数据层先行
2. __Java Service + Controller__ — 接口层
3. __Java 补充 Redis 依赖到 pom.xml__ — 集成消息发送
4. __前端 API 层 + KnowledgeBase.tsx__ — UI 实现
5. __Python consumer + parser__ — 文档处理
6. __联调验证__ — 端到端测试


# issue3
Issue 3: 文档处理与索引构建 - 实施计划
当前状态分析
已完成的工作（Issue 1 & 2）：

✅ 数据库 chunks 表已创建（含 embedding vector(1024)、search_vector TSVECTOR、HNSW 索引、GIN 索引、触发器）
✅ Python 文档处理消费者已实现（监听 Redis Streams，解析文档，更新状态）
✅ 文档解析器已实现（支持 txt/md/pdf/docx）
✅ 当前 consumer.py 中解析完文档后直接标记 completed，未做切分和向量化
实施计划
步骤 1: 创建文本切分模块 python/modules/document_processor/chunker.py
使用 LangChain 的 Semantic Chunking（语义切分）将文档内容切分为块：

使用 langchain_experimental.text_splitter.SemanticChunker 或基于嵌入的语义切分
配置合适的切分参数（chunk_size、chunk_overlap）
返回切分后的文本块列表
步骤 2: 创建向量化模块 python/modules/document_processor/embedder.py
使用 BGE-M3 模型生成 1024 维向量：

加载 BGE_M3_MODEL_PATH 配置的模型
提供 generate_embedding(text) 函数
支持批量向量化以提高效率
步骤 3: 重构 consumer.py 中的 process_document_task
将原来的"解析→标记完成"流程改为完整流程：

解析文档内容（已有）
语义切分 → 存入 chunks 表（含 content、metadata）
生成向量 → 更新 chunks 表的 embedding 列
更新文档状态为 completed
步骤 4: 创建 Java 侧文档处理状态回调 API
Python 处理完成后需要通知 Java 更新状态。有两种方案：

方案 A（推荐）：Python 直接更新数据库

Python 已有数据库连接池，直接 UPDATE documents 表
无需额外 API 调用，减少耦合
方案 B：Java 提供回调 API

Python 处理完成后调用 Java 的 REST API 通知状态变更
鉴于当前 Python 已经能直接操作数据库，推荐使用 方案 A。

步骤 5: 添加文档重试 API（Java 侧）
在 Java DocumentService 中添加重试方法：

将状态为 error 的文档重新发送 Redis Stream 消息
前端可调用重试接口
文件变更清单
文件	操作	说明
python/modules/document_processor/chunker.py	新建	语义切分模块
python/modules/document_processor/embedder.py	新建	BGE-M3 向量化模块
python/modules/document_processor/consumer.py	修改	集成切分+向量化流程
python/modules/document_processor/__init__.py	修改	导出新模块
python/modules/__init__.py	修改	导出新模块
java/document-service/.../DocumentService.java	修改	添加重试方法
java/document-service/.../DocumentController.java	修改	添加重试接口
验收标准对照
 上传文档后能在 30 秒内完成处理 — 语义切分 + BGE-M3 向量化，小文档应 < 30s
 chunks 表有正确的切分数据 — 语义切分后逐条 INSERT
 TSVECTOR 全文索引创建成功 — 已有触发器自动处理
 HNSW 索引创建成功 — 已有索引
 处理失败时状态为 error，支持手动重试 — 新增重试 API
技术细节
语义切分配置建议：

使用 HuggingFaceEmbeddings（BGE-M3）作为切分时的嵌入模型
breakpoint_threshold_type: "percentile"（百分位数断点）
最小 chunk 大小: 100 字符，最大: 1000 字符
向量化策略：

BGE-M3 模型加载后保持单例，避免重复加载
使用 model.encode() 批量处理
向量维度 1024，与数据库 schema 一致