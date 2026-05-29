package com.rag.chat.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/**
 * 一级缓存服务（Redis 精确匹配）
 * <p>
 * 功能：
 * 1. 问题规范化（统一小写、去除标点、去除多余空格）
 * 2. MD5 哈希作为 key
 * 3. 完整答案 + agent_type 作为 value（JSON格式），TTL 24 小时
 * 4. 缓存命中时模拟流式输出（按每5字符拆分小块）
 */
@Service
public class CacheService {

    private static final Logger logger = LoggerFactory.getLogger(CacheService.class);

    @Autowired
    private StringRedisTemplate redisTemplate;

    @Value("${cache.redis.ttl-hours:24}")
    private long cacheTtlHours;

    // 标点符号正则
    private static final Pattern PUNCTUATION_PATTERN = Pattern.compile("[\\p{P}\\p{S}]");
    // 多余空格正则
    private static final Pattern WHITESPACE_PATTERN = Pattern.compile("\\s+");

    // 模拟流式输出时每块字符数
    private static final int CHUNK_SIZE = 5;

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    /**
     * 规范化问题文本
     * 1. 去除首尾空格
     * 2. 统一小写
     * 3. 去除标点符号
     * 4. 合并多余空格
     */
    public String normalizeQuestion(String question) {
        if (question == null) {
            return "";
        }
        String normalized = question.trim()
                .toLowerCase()
                .replaceAll(PUNCTUATION_PATTERN.pattern(), "")
                .replaceAll(WHITESPACE_PATTERN.pattern(), " ");
        logger.debug("Normalized question: '{}' -> '{}'", question, normalized);
        return normalized;
    }

    /**
     * 生成缓存 key（MD5 哈希 + 知识库 ID）
     * 格式: cache:l1:<MD5>:<knowledge_base_id>
     * 当 knowledgeBaseId 为 null 时，使用 "none" 作为后缀
     */
    public String generateCacheKey(String normalizedQuestion, String knowledgeBaseId) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] digest = md.digest(normalizedQuestion.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : digest) {
                sb.append(String.format("%02x", b & 0xff));
            }
            String kbSuffix = (knowledgeBaseId != null && !knowledgeBaseId.isEmpty()) ? knowledgeBaseId : "none";
            String key = "cache:l1:" + sb + ":" + kbSuffix;
            logger.debug("Generated cache key: {}", key);
            return key;
        } catch (NoSuchAlgorithmException e) {
            logger.error("MD5 algorithm not available", e);
            throw new RuntimeException("MD5 algorithm not available", e);
        }
    }

    /**
     * 从缓存获取答案（兼容旧格式：纯文本）
     *
     * @param question 原始用户问题
     * @param knowledgeBaseId 知识库 ID（可选），用于按知识库隔离缓存
     * @return 缓存的答案，如果未命中返回 null
     */
    public String getCachedAnswer(String question, String knowledgeBaseId) {
        String normalized = normalizeQuestion(question);
        if (normalized.isEmpty()) {
            return null;
        }
        String key = generateCacheKey(normalized, knowledgeBaseId);
        try {
            String cachedValue = redisTemplate.opsForValue().get(key);
            if (cachedValue != null) {
                logger.info("L1 cache HIT for question: '{}' (key: {}, kbId: {})", question, key, knowledgeBaseId);
                // 尝试解析 JSON 格式，兼容旧格式（纯文本）
                try {
                    Map<String, String> map = OBJECT_MAPPER.readValue(cachedValue, Map.class);
                    return map.get("answer");
                } catch (JsonProcessingException e) {
                    // 旧格式：纯文本答案，直接返回
                    return cachedValue;
                }
            } else {
                logger.debug("L1 cache MISS for question: '{}' (key: {}, kbId: {})", question, key, knowledgeBaseId);
                return null;
            }
        } catch (Exception e) {
            logger.error("Redis get error for key: {}", key, e);
            return null;
        }
    }

    /**
     * 从缓存获取答案、agent_type 及 sources
     *
     * @param question 原始用户问题
     * @param knowledgeBaseId 知识库 ID（可选），用于按知识库隔离缓存
     * @return CacheEntry 包含 answer、agent_type 和 sources，如果未命中返回 null
     */
    public CacheEntry getCachedAnswerWithType(String question, String knowledgeBaseId) {
        String normalized = normalizeQuestion(question);
        if (normalized.isEmpty()) {
            return null;
        }
        String key = generateCacheKey(normalized, knowledgeBaseId);
        try {
            String cachedValue = redisTemplate.opsForValue().get(key);
            if (cachedValue != null) {
                logger.info("L1 cache HIT for question: '{}' (key: {}, kbId: {})", question, key, knowledgeBaseId);
                // 尝试解析 JSON 格式
                try {
                    Map<String, String> map = OBJECT_MAPPER.readValue(cachedValue, Map.class);
                    String answer = map.get("answer");
                    String agentType = map.get("agent_type");
                    String sources = map.get("sources");
                    if (answer != null) {
                        return new CacheEntry(answer, agentType != null ? agentType : "chat", sources);
                    }
                } catch (JsonProcessingException e) {
                    // 旧格式：纯文本答案，默认 agent_type 为 chat
                    logger.debug("Old format cache value (plain text), defaulting agent_type to chat");
                    return new CacheEntry(cachedValue, "chat", null);
                }
            } else {
                logger.debug("L1 cache MISS for question: '{}' (key: {}, kbId: {})", question, key, knowledgeBaseId);
            }
        } catch (Exception e) {
            logger.error("Redis get error for key: {}", key, e);
        }
        return null;
    }

    /**
     * 写入缓存（含 agent_type 和 sources）
     *
     * @param question  原始用户问题
     * @param answer    完整答案
     * @param agentType agent 类型（rag/chat）
     * @param sources   检索来源 JSON 字符串（可选）
     * @param knowledgeBaseId 知识库 ID（可选），用于按知识库隔离缓存
     */
    public void setCachedAnswer(String question, String answer, String agentType, String sources, String knowledgeBaseId) {
        String normalized = normalizeQuestion(question);
        if (normalized.isEmpty() || answer == null || answer.isEmpty()) {
            return;
        }
        String key = generateCacheKey(normalized, knowledgeBaseId);
        try {
            // 构建 JSON 格式：{"answer": "...", "agent_type": "rag", "sources": "..."}
            Map<String, Object> valueMap = new java.util.HashMap<>();
            valueMap.put("answer", answer);
            valueMap.put("agent_type", agentType != null ? agentType : "chat");
            if (sources != null && !sources.isEmpty()) {
                valueMap.put("sources", sources);
            }
            String jsonValue = OBJECT_MAPPER.writeValueAsString(valueMap);
            redisTemplate.opsForValue().set(key, jsonValue, cacheTtlHours, TimeUnit.HOURS);
            logger.info("L1 cache SET for question: '{}' (key: {}, agent_type={}, has_sources={}, kbId={}, ttl: {}h)",
                    question, key, agentType, sources != null && !sources.isEmpty(), knowledgeBaseId, cacheTtlHours);
        } catch (Exception e) {
            logger.error("Redis set error for key: {}", key, e);
        }
    }

    /**
     * 写入缓存（含 agent_type 和 knowledgeBaseId）
     *
     * @param question  原始用户问题
     * @param answer    完整答案
     * @param agentType agent 类型（rag/chat）
     * @param knowledgeBaseId 知识库 ID（可选）
     */
    public void setCachedAnswer(String question, String answer, String agentType, String knowledgeBaseId) {
        setCachedAnswer(question, answer, agentType, null, knowledgeBaseId);
    }

    /**
     * 写入缓存（兼容旧接口，默认 agent_type 为 chat，无 knowledgeBaseId）
     *
     * @param question 原始用户问题
     * @param answer   完整答案
     */
    public void setCachedAnswer(String question, String answer) {
        setCachedAnswer(question, answer, "chat", null, null);
    }

    /**
     * 缓存条目（包含答案、agent 类型和检索来源）
     */
    public static class CacheEntry {
        private final String answer;
        private final String agentType;
        private final String sources;

        public CacheEntry(String answer, String agentType) {
            this(answer, agentType, null);
        }

        public CacheEntry(String answer, String agentType, String sources) {
            this.answer = answer;
            this.agentType = agentType;
            this.sources = sources;
        }

        public String getAnswer() {
            return answer;
        }

        public String getAgentType() {
            return agentType;
        }

        public String getSources() {
            return sources;
        }
    }


    /**
     * 模拟流式输出：将完整答案按每5字符拆分为小块，依次发射
     * <p>
     * 每个小块包装为 SSE 格式：
     * event: message
     * data: {"type":"text","content":"..."}
     * <p>
     * 最后发送完成事件：
     * event: done
     * data: {"type":"done"}
     * <p>
     * 使用 Flux.interval 实现非阻塞延迟，避免 Thread.sleep 阻塞 WebFlux 事件循环。
     * 注意：使用 ObjectMapper 生成 JSON 字符串，确保所有特殊字符被正确转义，
     * 避免手动拼接 JSON 导致前端 JSON.parse 失败的问题。
     */
    public Flux<String> simulateStreamFromCache(String answer) {
        if (answer == null || answer.isEmpty()) {
            return Flux.just(
                    "event: done\ndata: {\"type\":\"done\"}\n\n"
            );
        }

        // 将答案按 CHUNK_SIZE 拆分为多个块
        List<String> chunks = new ArrayList<>();
        int length = answer.length();
        int start = 0;
        while (start < length) {
            int end = Math.min(start + CHUNK_SIZE, length);
            chunks.add(answer.substring(start, end));
            start = end;
        }

        // 使用 Flux.fromIterable + delayElements 实现非阻塞延迟发射
        // 每 50ms 发射一个 chunk，然后映射为 SSE 格式的字符串
        // 注意：使用 fromIterable 而非 interval，避免 interval 异步时序导致最后一个 chunk 丢失
        return Flux.fromIterable(chunks)
                .delayElements(Duration.ofMillis(50))
                .map(chunk -> {
                    try {
                        String jsonData = OBJECT_MAPPER.writeValueAsString(Map.of(
                                "type", "text",
                                "content", chunk
                        ));
                        return String.format("event: message\ndata: %s\n\n", jsonData);
                    } catch (JsonProcessingException e) {
                        logger.error("Error serializing chunk", e);
                        return "";
                    }
                })
                .concatWithValues("event: done\ndata: {\"type\":\"done\"}\n\n");
    }

}
