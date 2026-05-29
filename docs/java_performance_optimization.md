# Java 侧性能优化方案

> 基于对 Java 侧全部核心代码的深度审查
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
| 1 | **ChatController DataBuffer 优化** | chat-service | 🔴 | 减少 GC 压力，提升高并发吞吐 | ⭐ 极小 | 低 |
| 2 | **关闭生产环境 JPA show-sql** | document-service | 🔴 | 消除 SQL 日志性能开销 | ⭐ 极小 | 低 |
| 3 | **Gateway 熔断/重试/压缩** | gateway | 🟡 | 提升系统稳定性 | ⭐⭐ 中等 | 中 |
| 4 | **缓存命中时对话保存并发控制** | chat-service | 🟡 | 防止高并发压垮 Python 端 | ⭐ 极小 | 低 |
| 5 | **删除知识库 N+1 查询优化** | document-service | 🟡 | 减少 SQL 查询次数 | ⭐ 极小 | 低 |
| 6 | **CacheService ObjectMapper 静态化** | chat-service | 🟢 | 微优化，遵循最佳实践 | ⭐ 极小 | 低 |
| 7 | **Tomcat 线程池调优** | document-service | 🟢 | 提升文件上传并发能力 | ⭐ 极小 | 低 |

### 优先级说明

- **🔴 高优先级**: 直接影响性能或存在明显问题，建议优先实施
- **🟡 中优先级**: 提升系统稳定性或效率，可在高优先级完成后实施
- **🟢 低优先级**: 微优化或最佳实践，可在迭代中逐步完善
- **改动量**: ⭐（极小，<10行） ⭐⭐（中等，10-50行）

---

## 二、各优化项详细说明

---

### 🥇 TOP 1：ChatController DataBuffer 优化

#### 问题描述

当前每个 DataBuffer 都创建新的 byte 数组并手动 release：

```java
// 当前代码 - 频繁分配 byte 数组，增加 GC 压力
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

- `java/chat-service/src/main/java/com/rag/chat/controller/ChatController.java`（第 140-148 行）

#### 优化方案

使用 `buffer.asByteBuffer()` 直接获取 `ByteBuffer` 视图，避免创建中间 byte 数组：

```java
// 优化后 - 使用 asByteBuffer() 避免数组分配
return response.bodyToFlux(DataBuffer.class)
    .map(buffer -> {
        String chunk = StandardCharsets.UTF_8.decode(buffer.asByteBuffer()).toString();
        DataBufferUtils.release(buffer);
        return chunk;
    });
```

> ⚠️ **为什么不使用 `bodyToFlux(String.class)`？**
>
> Spring WebFlux 的 `StringDecoder` 会累积 buffer 直到遇到完整分隔符才发射，对于 SSE 流式场景，这会导致数据被缓冲而不是实时推送，**破坏流式效果**。因此必须使用 `bodyToFlux(DataBuffer.class)` 逐块透传。

#### 预期效果

- 减少 GC 压力（避免每次分配 byte 数组）
- 代码更简洁
- 高并发下吞吐量提升

---

### 🥇 TOP 2：关闭生产环境 JPA show-sql

#### 问题描述

`document-service` 的 `application.yml` 中开启了 JPA SQL 日志：

```yaml
spring:
  jpa:
    show-sql: true  # 生产环境不应开启
```

这会导致：
- 每次 SQL 执行都打印到日志，产生大量 I/O 开销
- 可能泄露表结构和查询模式等敏感信息

#### 涉及文件

- `java/document-service/src/main/resources/application.yml`（第 13 行）

#### 优化方案

```yaml
spring:
  jpa:
    show-sql: false  # 关闭 SQL 日志
    # 或使用 Profile 区分环境：
    # 开发环境保留，生产环境关闭
```

如果需要在开发环境保留，建议使用 Spring Profile：

```yaml
# application-dev.yml
spring:
  jpa:
    show-sql: true

# application-prod.yml
spring:
  jpa:
    show-sql: false
```

#### 预期效果

- 消除不必要的 SQL 日志 I/O 开销
- 避免敏感信息泄露

---

### 🥇 TOP 3：Gateway 熔断/重试/压缩

#### 问题描述

当前 Gateway 直接路由到固定地址，没有任何容错机制：

```yaml
routes:
  - id: chat-service
    uri: http://localhost:8082  # 硬编码地址
```

- 没有熔断机制（下游服务故障会级联）
- 没有重试机制（临时故障导致请求失败）
- 没有响应压缩（浪费带宽）

#### 涉及文件

- `java/gateway/pom.xml`
- `java/gateway/src/main/resources/application.yml`

#### 优化方案

**步骤 1：添加依赖**

```xml
<!-- pom.xml 中添加 Resilience4j 依赖 -->
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-circuitbreaker-reactor-resilience4j</artifactId>
</dependency>
```

**步骤 2：配置熔断、重试和压缩**

```yaml
spring:
  cloud:
    gateway:
      # 启用响应压缩
      compression:
        enabled: true
      # ... 现有路由配置 ...
      default-filters:
        - name: Retry
          args:
            retries: 2
            statuses: BAD_GATEWAY, SERVICE_UNAVAILABLE, GATEWAY_TIMEOUT
            methods: GET, POST
            backoff:
              firstBackoff: 500ms
              maxBackoff: 3s
              factor: 2
      routes:
        - id: document-service
          uri: http://localhost:8081
          predicates:
            - Path=/api/knowledge-bases/**,/api/documents/**
          filters:
            - StripPrefix=1
            - name: CircuitBreaker
              args:
                name: documentServiceCircuitBreaker
                fallbackUri: forward:/fallback/document
        - id: chat-service
          uri: http://localhost:8082
          predicates:
            - Path=/api/chat/**
          filters:
            - name: DedupeResponseHeader
              args:
                name: Access-Control-Allow-Origin Access-Control-Allow-Credentials
                strategy: RETAIN_FIRST
            - name: CircuitBreaker
              args:
                name: chatServiceCircuitBreaker
                fallbackUri: forward:/fallback/chat
        - id: python-conversations
          uri: http://localhost:8000
          predicates:
            - Path=/api/conversations/**
          filters:
            - name: CircuitBreaker
              args:
                name: pythonServiceCircuitBreaker
                fallbackUri: forward:/fallback/python
```

**步骤 3：配置 Resilience4j 参数**

```yaml
# resilience4j 配置
resilience4j:
  circuitbreaker:
    configs:
      default:
        slidingWindowSize: 10
        minimumNumberOfCalls: 5
        failureRateThreshold: 50
        waitDurationInOpenState: 10s
        permittedNumberOfCallsInHalfOpenState: 3
  timelimiter:
    configs:
      default:
        timeoutDuration: 30s
```

> ⚠️ **为什么不使用 `lb://` 负载均衡？**
>
> 当前项目没有服务注册中心（Eureka/Consul），直接使用 `lb://` 前缀会导致启动报错。如果后续需要水平扩展，可以引入服务注册中心后再启用负载均衡。
>
> ⚠️ **为什么不使用 `RequestRateLimiter`？**
>
> `RequestRateLimiter` 需要 Redis 依赖（`spring-boot-starter-data-redis-reactive`），且当前 Gateway 的 pom.xml 中没有引入。如果后续需要限流功能，可以单独添加。

#### 预期效果

- 下游服务临时故障时自动重试（最多 2 次，指数退避）
- 下游服务持续故障时熔断，避免级联故障
- 响应压缩减少带宽占用

---

### 🥇 TOP 4：缓存命中时对话保存并发控制

#### 问题描述

当一级缓存命中时，`ChatController` 使用 `subscribe()` 异步保存对话到 Python 端：

```java
// 当前代码 - 无限制异步请求
pythonWebClient.post()
        .uri("/api/conversations/save_message")
        .bodyValue(saveBody)
        .retrieve()
        .bodyToMono(String.class)
        .subscribe(
                result -> logger.info("Saved conversation via L1 cache: {}", result),
                error -> logger.error("Failed to save conversation via L1 cache: {}", error.getMessage())
        );
```

高并发缓存命中时，大量异步 HTTP 请求可能压垮 Python 端。

#### 涉及文件

- `java/chat-service/src/main/java/com/rag/chat/controller/ChatController.java`（第 77-85 行）

#### 优化方案

使用 `Scheduler` 限制并发：

```java
import reactor.core.scheduler.Scheduler;
import reactor.core.scheduler.Schedulers;

// 在类中定义限流调度器
private static final Scheduler cacheSaveScheduler = Schedulers.newBoundedElastic(5, 100, "cache-save");

// 使用限流调度器
pythonWebClient.post()
        .uri("/api/conversations/save_message")
        .bodyValue(saveBody)
        .retrieve()
        .bodyToMono(String.class)
        .subscribeOn(cacheSaveScheduler)
        .subscribe(
                result -> logger.info("Saved conversation via L1 cache: {}", result),
                error -> logger.error("Failed to save conversation via L1 cache: {}", error.getMessage())
        );
```

或者使用更简单的信号量方式：

```java
import java.util.concurrent.Semaphore;

private static final Semaphore cacheSaveSemaphore = new Semaphore(5);

// 使用信号量
if (cacheSaveSemaphore.tryAcquire()) {
    pythonWebClient.post()
            .uri("/api/conversations/save_message")
            .bodyValue(saveBody)
            .retrieve()
            .bodyToMono(String.class)
            .subscribe(
                    result -> {
                        cacheSaveSemaphore.release();
                        logger.info("Saved conversation via L1 cache: {}", result);
                    },
                    error -> {
                        cacheSaveSemaphore.release();
                        logger.error("Failed to save conversation via L1 cache: {}", error.getMessage());
                    }
            );
} else {
    logger.warn("Cache save queue full, skipping conversation save for: {}", message);
}
```

#### 预期效果

- 限制并发异步请求数，保护 Python 端不被突发流量打满
- 超出限制时优雅降级（跳过保存，不影响主流程）

---

### 🥇 TOP 5：删除知识库 N+1 查询优化

#### 问题描述

`KnowledgeBaseService.delete()` 存在多余的查询：

```java
@Transactional
public void delete(UUID id) {
    kbRepo.findById(id).ifPresent(kb -> {       // 查询 1：findById
        docRepo.deleteByKnowledgeBaseId(id);     // 查询 2-N：逐条 DELETE
        try {
            fileStorageService.deleteKnowledgeBaseFiles(id);
        } catch (Exception ignored) {}
        kbRepo.delete(kb);                       // 查询 N+1：delete
    });
}
```

- `findById` + `delete` 两次查询，实际上可以直接 `deleteById`
- `deleteByKnowledgeBaseId` 在 JpaRepository 中默认是逐条 DELETE，不是批量操作

#### 涉及文件

- `java/document-service/src/main/java/com/rag/document/service/KnowledgeBaseService.java`（第 48-60 行）

#### 优化方案

```java
@Transactional
public void delete(UUID id) {
    // 直接删除文档记录（使用自定义 @Modifying 批量删除）
    docRepo.deleteByKnowledgeBaseId(id);
    // 删除物理文件
    try {
        fileStorageService.deleteKnowledgeBaseFiles(id);
    } catch (Exception ignored) {}
    // 直接 deleteById，避免多余的 findById
    kbRepo.deleteById(id);
}
```

同时优化 `DocumentRepository`，使用批量删除：

```java
@Repository
public interface DocumentRepository extends JpaRepository<Document, UUID> {
    List<Document> findByKnowledgeBaseId(UUID knowledgeBaseId);
    
    @Modifying
    @Query("DELETE FROM Document d WHERE d.knowledgeBaseId = :kbId")
    void deleteByKnowledgeBaseId(@Param("kbId") UUID knowledgeBaseId);
}
```

> ⚠️ 注意：`@Modifying` 批量删除不会触发 JPA 的级联删除和 `@PreRemove` 回调。如果 Document 有关联的 Chunk 实体需要级联删除，需要确保数据库层面有 `ON DELETE CASCADE` 约束，或者手动删除关联数据。

#### 预期效果

- 减少 1 次不必要的 `findById` 查询
- `deleteByKnowledgeBaseId` 从逐条 DELETE 变为单条批量 DELETE
- 知识库删除操作速度提升

---

### 🥇 TOP 6：CacheService ObjectMapper 静态化

#### 问题描述

`CacheService` 中的 `ObjectMapper` 虽然是 `final` 实例变量，但可以进一步优化为 `static final`：

```java
@Service
public class CacheService {
    private final ObjectMapper objectMapper = new ObjectMapper();  // 每个实例一个
}
```

`ObjectMapper` 是线程安全的，可以复用同一个实例。

#### 涉及文件

- `java/chat-service/src/main/java/com/rag/chat/service/CacheService.java`（第 47 行）

#### 优化方案

```java
@Service
public class CacheService {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
}
```

或者注入 Spring 管理的 ObjectMapper Bean（推荐，因为 Spring Boot 自动配置的 ObjectMapper 已经包含了常用的模块和配置）：

```java
@Service
public class CacheService {
    @Autowired
    private ObjectMapper objectMapper;  // 注入 Spring 管理的 Bean
}
```

#### 预期效果

- 遵循最佳实践
- 如果后续需要自定义 ObjectMapper 配置（如日期格式），Spring 注入方式更灵活

---

### 🥇 TOP 7：Tomcat 线程池调优

#### 问题描述

`document-service` 使用 Servlet 栈（`spring-boot-starter-web`），默认 Tomcat 线程池为 `max=200`。虽然默认值已经较大，但在高并发文件上传场景下，每个上传请求会占用线程较长时间（文件写入磁盘 + Redis Stream 写入），可能导致线程池耗尽。

#### 涉及文件

- `java/document-service/src/main/resources/application.yml`

#### 优化方案

根据实际并发需求调整 Tomcat 线程池：

```yaml
server:
  port: 8081
  tomcat:
    threads:
      max: 50        # 根据并发需求调整，默认 200
      min-spare: 10  # 最小空闲线程数
    max-connections: 100   # 最大连接数
    accept-count: 50       # 请求队列长度
```

> ⚠️ 如果后续需要更高的上传并发，建议将 `document-service` 迁移到 WebFlux（非阻塞 IO），但这属于较大改动，需要评估收益。

#### 预期效果

- 更合理的线程资源分配
- 避免突发流量下线程池耗尽

---

## 三、实施路线图

### 第一阶段：快速见效（1 天）

| 优化项 | 改动量 | 说明 |
|--------|:------:|------|
| TOP 1：DataBuffer 优化 | 1 行 | 修改 ChatController.java |
| TOP 2：关闭 show-sql | 1 行 | 修改 application.yml |
| TOP 6：ObjectMapper 静态化 | 1 行 | 修改 CacheService.java |

### 第二阶段：稳定性提升（1-2 天）

| 优化项 | 改动量 | 说明 |
|--------|:------:|------|
| TOP 3：Gateway 熔断/重试/压缩 | pom.xml + application.yml | 添加依赖和配置 |
| TOP 4：缓存保存并发控制 | 10-15 行 | 添加 Semaphore 或 Scheduler |

### 第三阶段：架构优化（1 天）

| 优化项 | 改动量 | 说明 |
|--------|:------:|------|
| TOP 5：删除知识库 N+1 优化 | 5 行 | 修改 Service + Repository |
| TOP 7：Tomcat 线程池调优 | 5 行 | 修改 application.yml |

---

## 附录：已排除的优化项

以下为审查中考虑过但**不建议优先实施**的项：

| 优化项 | 排除原因 |
|--------|---------|
| document-service 迁移 WebFlux | 改动过大（涉及文件上传、JPA、RedisTemplate 等），收益不确定 |
| Gateway 添加 `lb://` 负载均衡 | 需要引入服务注册中心（Eureka/Consul），当前项目架构不支持 |
| Gateway 添加 `RequestRateLimiter` | 需要额外引入 Redis Reactive 依赖，当前限流需求不迫切 |
| Redis 连接池调优 | 当前使用 Lettuce 默认连接池，在低并发下无明显问题 |
| 添加全局异常处理器 | 属于代码质量优化，非性能优化 |
