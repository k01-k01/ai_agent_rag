package com.rag.document.controller;

import com.rag.document.dto.CreateKnowledgeBaseRequest;
import com.rag.document.model.KnowledgeBase;
import com.rag.document.service.KnowledgeBaseService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.*;

@RestController
@RequestMapping("/knowledge-bases")
public class KnowledgeBaseController {

    private final KnowledgeBaseService kbService;

    public KnowledgeBaseController(KnowledgeBaseService kbService) {
        this.kbService = kbService;
    }

    @GetMapping
    public ResponseEntity<List<KnowledgeBase>> listAll() {
        return ResponseEntity.ok(kbService.listAll());
    }

    @GetMapping("/{id}")
    public ResponseEntity<KnowledgeBase> getById(@PathVariable UUID id) {
        return kbService.getById(id)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @PostMapping
    public ResponseEntity<KnowledgeBase> create(@RequestBody CreateKnowledgeBaseRequest request) {
        if (request.getName() == null || request.getName().trim().isEmpty()) {
            return ResponseEntity.badRequest().build();
        }
        KnowledgeBase kb = kbService.create(request.getName().trim());
        return ResponseEntity.ok(kb);
    }

    @PutMapping("/{id}")
    public ResponseEntity<KnowledgeBase> update(@PathVariable UUID id,
                                                @RequestBody CreateKnowledgeBaseRequest request) {
        if (request.getName() == null || request.getName().trim().isEmpty()) {
            return ResponseEntity.badRequest().build();
        }
        return kbService.update(id, request.getName().trim())
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Map<String, String>> delete(@PathVariable UUID id) {
        if (kbService.getById(id).isEmpty()) {
            return ResponseEntity.notFound().build();
        }
        kbService.delete(id);
        Map<String, String> result = new HashMap<>();
        result.put("message", "Knowledge base deleted successfully");
        return ResponseEntity.ok(result);
    }
}