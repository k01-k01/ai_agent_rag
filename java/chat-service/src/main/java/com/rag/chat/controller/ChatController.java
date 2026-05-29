package com.rag.chat.controller;

import com.rag.chat.dto.ChatRequest;
import com.rag.chat.service.CacheService;
import com.rag.chat.service.CacheService.CacheEntry;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.buffer.DataBuffer;
import org.springframework.core.io.buffer.DataBufferUtils;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Semaphore;



@RestController
@RequestMapping("/api/chat")
public class ChatController {

    private static final Logger logger = LoggerFactory.getLogger(ChatController.class);

    // 限制缓存命中时异步保存对话的并发数，防止高并发下压垮 Python 端
    private static final Semaphore CACHE_SAVE_SEMAPHORE = new Semaphore(5);

    @Autowired
    private WebClient pythonWebClient;

    @Autowired
    private CacheService cacheService;

    /**
     * POST 方式 SSE 流式聊天接口
     * 前端通过 fetch + ReadableStream 调用
     * <p>
     * 缓存策略：
     * 1. 先检查一级缓存（Redis 精确匹配）
     * 2. 命中则模拟流式输出返回
     * 3. 未命中则调用 Python 侧接口
     * <p>
     * 注意：使用 TEXT_PLAIN_VALUE 而非 TEXT_EVENT_STREAM_VALUE，
     * 因为 Python 后端已经返回了完整的 SSE 格式（event:/data: 行），
     * 如果使用 TEXT_EVENT_STREAM_VALUE，Spring WebFlux 会对 Flux<String>
     * 的每个元素自动添加 "data:" 前缀，导致 SSE 格式被破坏。
     */
    @PostMapping(value = "/stream", produces = MediaType.TEXT_PLAIN_VALUE)
    public Flux<String> chatStream(@RequestBody ChatRequest request) {
        String message = request.getMessage();
        String knowledgeBaseId = request.getKnowledgeBaseId();
        String conversationId = request.getConversationId();

        logger.debug("Chat request: message={}, kbId={}, convId={}",
                message, knowledgeBaseId, conversationId);

        // ========== 一级缓存检查（Redis 精确匹配，按知识库 ID 隔离） ==========
        CacheEntry cacheEntry = cacheService.getCachedAnswerWithType(message, knowledgeBaseId);
        if (cacheEntry != null) {
            String cachedAnswer = cacheEntry.getAnswer();
            String agentType = cacheEntry.getAgentType();
            logger.info("L1 cache HIT for message: '{}', agent_type={}, returning cached answer (length: {})",
                    message, agentType, cachedAnswer.length());

            // ===== 异步保存对话到数据库（不阻塞 SSE 流响应） =====
            // 使用信号量控制并发，超出限制时跳过保存（不影响主流程）
            if (CACHE_SAVE_SEMAPHORE.tryAcquire()) {
                Map<String, Object> saveBody = new java.util.HashMap<>();
                saveBody.put("message", message);
                saveBody.put("answer", cachedAnswer);
                saveBody.put("agent_type", agentType);
                saveBody.put("sources", cacheEntry.getSources());
                if (conversationId != null && !conversationId.isEmpty()) {
                    saveBody.put("conversation_id", conversationId);
                }

                pythonWebClient.post()
                        .uri("/api/conversations/save_message")
                        .bodyValue(saveBody)
                        .retrieve()
                        .bodyToMono(String.class)
                        .subscribe(
                                result -> {
                                    CACHE_SAVE_SEMAPHORE.release();
                                    logger.info("Saved conversation via L1 cache: {}", result);
                                },
                                error -> {
                                    CACHE_SAVE_SEMAPHORE.release();
                                    logger.error("Failed to save conversation via L1 cache: {}", error.getMessage());
                                }
                        );
            } else {
                logger.warn("Cache save queue full, skipping conversation save for message: '{}'", message);
            }

            // 构建事件列表：先发送 agent 类型事件
            List<String> events = new ArrayList<>();
            events.add(String.format(
                    "event: agent\ndata: {\"type\":\"agent\",\"content\":\"%s\"}\n\n",
                    agentType
            ));

            // 如果有 conversationId，发送 conversation_id 事件
            if (conversationId != null && !conversationId.isEmpty()) {
                events.add(String.format(
                        "event: conversation_id\ndata: {\"type\":\"conversation_id\",\"content\":\"%s\"}\n\n",
                        conversationId
                ));
            }

            // 发送缓存命中提示
            events.add("event: message\ndata: {\"type\":\"text\",\"content\":\"⚡ [命中一级缓存 - 精确匹配]\\n\\n\"}\n\n");

            // 分开发射每个事件，然后模拟流式输出
            return Flux.fromIterable(events)
                    .concatWith(cacheService.simulateStreamFromCache(cachedAnswer));
        }


        logger.debug("L1 cache MISS for message: '{}', calling Python service via POST", message);

        // ========== 一级缓存未命中，调用 Python 侧接口（使用 POST 避免 URL 编码问题） ==========
        // 构建请求体
        Map<String, Object> pythonRequestBody = new java.util.HashMap<>();
        pythonRequestBody.put("message", message);
        if (knowledgeBaseId != null && !knowledgeBaseId.isEmpty()) {
            pythonRequestBody.put("knowledge_base_id", knowledgeBaseId);
        }
        if (conversationId != null && !conversationId.isEmpty()) {
            pythonRequestBody.put("conversation_id", conversationId);
        }

        logger.debug("Calling Python SSE via POST: /api/chat/stream, body={}", pythonRequestBody);

        // 直接透传 Python 的 SSE 流，不做任何解析和重组
        // 使用 TEXT_PLAIN 避免 Spring 对 SSE 格式的二次包装
        return pythonWebClient.post()
                .uri("/api/chat/stream")
                .bodyValue(pythonRequestBody)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .exchangeToFlux(response -> {
                    if (!response.statusCode().is2xxSuccessful()) {
                        logger.error("Python service returned status: {}", response.statusCode());
                        return Flux.just(
                                "event: error\ndata: {\"type\":\"error\",\"content\":\"Python服务返回错误: " + response.statusCode() + "\"}\n\n");
                    }
                    // 直接透传：将每个 DataBuffer 转换为字符串后直接发射
                    // 使用 map 而非 flatMap，保证 DataBuffer 的处理顺序与接收顺序一致
                    return response.bodyToFlux(DataBuffer.class)
                            .map(buffer -> {
                                String chunk = StandardCharsets.UTF_8.decode(buffer.asByteBuffer()).toString();
                                DataBufferUtils.release(buffer);
                                logger.trace("SSE raw chunk: {}", chunk);
                                return chunk;
                            });
                })
                .doOnNext(data -> logger.trace("SSE forwarded chunk: {}", data))
                .doOnError(e -> logger.error("Error calling Python SSE: {}", e.getMessage()))
                .onErrorResume(e -> Flux.just(
                        "event: error\ndata: {\"type\":\"error\",\"content\":\"服务暂时不可用，请稍后再试。\"}\n\n"
                ));

    }

    /**
     * GET 方式 SSE 流式聊天接口（用于测试）
     */
    @GetMapping(value = "/stream", produces = MediaType.TEXT_PLAIN_VALUE)
    public Flux<String> chatStreamGet(
            @RequestParam String message,
            @RequestParam(required = false) String knowledgeBaseId,
            @RequestParam(required = false) String conversationId) {

        ChatRequest request = new ChatRequest(message, knowledgeBaseId, conversationId);
        return chatStream(request);
    }
}
