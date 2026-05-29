# 个人RAG知识库项目

## 1.前端

前端react+vite+ts+tailwind，有一个知识库管理页面和一个聊天页面，知识库管理页面可供用户创建不同的知识库并上传文档，知识库和其中的文档都可以进行增添和删除；聊天界面可供用户选择不同的知识库进行提问，系统流式输出回答和多轮对话。回答中还要显示调用了python侧的rag agent还是chat agent，若调用了rag agent，要显示出检索来源。

## 2.后端

后端java（jdk17）+python。

### 2.1java侧

 java侧做三个springboot微服务，Spring Cloud Gateway、document-service、chat-service。

#### 2.1.1对于用户提问的缓存机制

对于用户的提问进行两级缓存机制：java chat-service侧一级缓存（哈希匹配）、python侧二级缓存（语义匹配）。

#### 2.1.2document-service

实现文档的上传与存储，支持txt、md、pdf、word格式的文档上传。异步通知 Python 文档处理模块。

#### 2.1.3chat-service

- **流式输出**：使用 Spring WebFlux + WebClient，将前端 SSE 请求透传给 Python FastAPI 的 SSE 接口。
- **一级缓存（Redis 精确匹配）**：  
  将用户问题规范化后作为 key，完整答案作为 value 存入 Redis。  
  命中时，将缓存的完整答案拆分为多个小块（如每 5 个字符）依次发送，模拟流式输出。  
  未命中时，调用 Python 侧接口，并将最终返回的答案异步写入 Redis（TTL 24 小时）。

#### 

## 2.2python侧

python侧包含三个模块，1.文档处理模块   2.二级缓存模块   3.多agents协作模块。

#### 2.2.1文档处理模块

该模块对 Java 侧上传的文档进行异步处理，流程如下：

1. **文本切分（chunk）**  
   使用 LangChain 框架将文档按语义切分成多个文本块（chunk），每个 chunk 作为一条记录存入 PostgreSQL 的 `chunks` 表中（包含字段：`id`、`knowledge_base_id`、`content`、`metadata` 等）。
2. **全文搜索索引构建（TSVECTOR）**  
   利用 PostgreSQL 原生的 `tsvector` 类型和 `GIN` 索引，在 `chunks` 表的 `search_vector` 字段上创建全文检索索引。Python 侧将 chunk 内容同步到 `search_vector` 字段，用于支持高效的关键词检索。
3. **向量索引构建（BGE-M3）**  
   使用 BGE-M3 模型为每个 chunk 生成 1024 维的稠密向量，并将向量存入 `chunks` 表的 `embedding` 列（数据类型为 `vector`，由 `pgvector` 扩展提供）。然后在 `embedding` 列上创建 HNSW 索引，用于支持向量相似度检索。

#### 2.2.2二级缓存模块

当 Java 侧一级缓存未命中时，Python 侧首先进行二级语义缓存检查：  
使用 BGE-M3 将用户问题向量化，在 pgvector 中检索相似历史问题，若相似度 > 0.85 则直接流式返回缓存的答案（同样拆分为小块模拟流式）。  

**未命中**二级缓存时，进入多 agents 模块（2.2.3）。无论最终由 rag agent 还是 chat agent 生成答案，均异步执行以下操作：  

1. 将问题和答案存入 pgvector（二级缓存）。  
2. 通知 Java chat-service 将答案写入 Redis（一级缓存）。

#### 2.2.3多agents协作模块

多agents协作模块使用langgraph框架，包含三个agents：router agent、rag agent、chat agent，每个agent都基于langchain框架搭建。router agent实现路由，判断用户的提问是普通问答还是需要检索知识库再回答，普通问答则路由到chat agent，检索知识库后再问答就路由到rag agent。

##### 2.2.3.1rag agent说明

rag agent使用langchain框架实现：用户提问、混合检索：关键词检索（TSVECTOR 全文搜索）+ 向量检索（pgvector）、结果融合 （RRF算法）、Reranker 精排、LLM回答。

## 3.配置说明

数据库使用postgresql+tsvector+pgvector，数据库密码2wsxcde3，redis缓存（docker），LLM调用deepseek的api。bge-m3模型地址在D:\project\models\bge-m3，reranker模型地址在D:\project\models\bge-reranker-v2-m3。
