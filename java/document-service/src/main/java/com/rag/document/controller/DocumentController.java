package com.rag.document.controller;

import com.rag.document.model.Document;
import com.rag.document.service.DocumentService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.*;

@RestController
@RequestMapping("/knowledge-bases/{kbId}/documents")
public class DocumentController {

    private final DocumentService docService;

    public DocumentController(DocumentService docService) {
        this.docService = docService;
    }

    @GetMapping
    public ResponseEntity<List<Document>> listDocuments(@PathVariable UUID kbId) {
        return ResponseEntity.ok(docService.listByKnowledgeBase(kbId));
    }

    @PostMapping
    public ResponseEntity<?> uploadDocument(@PathVariable UUID kbId,
                                            @RequestParam("file") MultipartFile file) {
        if (file.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "File is empty"));
        }
        try {
            Document doc = docService.uploadDocument(kbId, file);
            return ResponseEntity.ok(doc);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        } catch (Exception e) {
            return ResponseEntity.internalServerError()
                    .body(Map.of("error", "Upload failed: " + e.getMessage()));
        }
    }

    @DeleteMapping("/{docId}")
    public ResponseEntity<?> deleteDocument(@PathVariable UUID kbId,
                                            @PathVariable UUID docId) {
        try {
            docService.deleteDocument(kbId, docId);
            return ResponseEntity.ok(Map.of("message", "Document deleted successfully"));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        } catch (Exception e) {
            return ResponseEntity.internalServerError()
                    .body(Map.of("error", "Delete failed: " + e.getMessage()));
        }
    }

    /**
     * 重试处理失败的文档（状态为 error 的文档）。
     * 将文档重新发送到 Redis Stream 进行处理。
     */
    @PostMapping("/{docId}/retry")
    public ResponseEntity<?> retryDocument(@PathVariable UUID kbId,
                                           @PathVariable UUID docId) {
        try {
            Document doc = docService.retryDocument(kbId, docId);
            return ResponseEntity.ok(doc);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        } catch (Exception e) {
            return ResponseEntity.internalServerError()
                    .body(Map.of("error", "Retry failed: " + e.getMessage()));
        }
    }
}


