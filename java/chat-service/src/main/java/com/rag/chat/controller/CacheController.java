package com.rag.chat.controller;

import com.rag.chat.service.CacheService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * 缓存回调控制器
 * <p>
 * 接收 Python 侧的通知，将答案写入 Redis 一级缓存。
 * Python 侧在 Agent 生成答案后，异步调用此接口。
 */
@RestController
@RequestMapping("/api/chat/cache")
public class CacheController {

    private static final Logger logger = LoggerFactory.getLogger(CacheController.class);

    @Autowired
    private CacheService cacheService;

    @Autowired
    private StringRedisTemplate redisTemplate;

    /**
     * 设置缓存（由 Python 侧在 Agent 生成答案后调用）
     * <p>
     * 请求体: {"question": "用户问题", "answer": "完整答案", "agent_type": "rag", "sources": "[...]", "knowledge_base_id": "uuid"}
     */
    @PostMapping("/set")
    public ResponseEntity<Map<String, Object>> setCache(@RequestBody Map<String, String> request) {
        String question = request.get("question");
        String answer = request.get("answer");
        String agentType = request.get("agent_type");
        String sources = request.get("sources");
        String knowledgeBaseId = request.get("knowledge_base_id");

        if (question == null || question.isEmpty()) {
            logger.warn("Cache set request with empty question");
            return ResponseEntity.badRequest().body(Map.of(
                    "success", false,
                    "error", "question is required"
            ));
        }

        if (answer == null || answer.isEmpty()) {
            logger.warn("Cache set request with empty answer for question: {}", question);
            return ResponseEntity.badRequest().body(Map.of(
                    "success", false,
                    "error", "answer is required"
            ));
        }

        // URL 解码：Python 端传过来的 question 可能是 URL 编码后的字符串
        // 需要解码为正常中文，确保与前端请求的 question 生成的 MD5 key 一致
        String decodedQuestion = URLDecoder.decode(question, StandardCharsets.UTF_8);
        if (!decodedQuestion.equals(question)) {
            logger.debug("URL decoded question: '{}' -> '{}'", question, decodedQuestion);
            question = decodedQuestion;
        }

        // 写入缓存（含 agent_type、sources 和 knowledge_base_id）
        cacheService.setCachedAnswer(question, answer, agentType, sources, knowledgeBaseId);

        logger.info("Cache set callback received - question: {}, agent_type={}, has_sources={}, kbId={}, answer length: {}",
                question, agentType, sources != null && !sources.isEmpty(), knowledgeBaseId, answer.length());

        return ResponseEntity.ok(Map.of(
                "success", true,
                "message", "Cache set successfully"
        ));
    }

    /**
     * 清空一级缓存（Redis）
     * 使用 FLUSHDB 清空当前数据库的所有 key
     */
    @PostMapping("/l1/clear")
    public ResponseEntity<Map<String, Object>> clearL1Cache() {
        try {
            // 获取 Redis 连接并清空当前数据库
            redisTemplate.getConnectionFactory().getConnection().flushDb();
            logger.info("L1 cache (Redis) cleared successfully");
            return ResponseEntity.ok(Map.of(
                    "success", true,
                    "message", "一级缓存（Redis）已清空"
            ));
        } catch (Exception e) {
            logger.error("Failed to clear L1 cache: {}", e.getMessage(), e);
            return ResponseEntity.status(500).body(Map.of(
                    "success", false,
                    "error", "清空一级缓存失败: " + e.getMessage()
            ));
        }
    }

}
