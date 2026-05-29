package com.rag.document.service;

import com.rag.document.model.Document;
import com.rag.document.repository.DocumentRepository;
import org.springframework.data.redis.connection.stream.ObjectRecord;
import org.springframework.data.redis.connection.stream.StreamRecords;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.*;

@Service
public class DocumentService {

    private static final Set<String> ALLOWED_EXTENSIONS = Set.of("txt", "md", "pdf", "doc", "docx");
    private static final String REDIS_STREAM_KEY = "documents:processing";

    private final DocumentRepository docRepo;
    private final FileStorageService fileStorageService;
    private final RedisTemplate<String, Object> redisTemplate;

    public DocumentService(DocumentRepository docRepo,
                           FileStorageService fileStorageService,
                           RedisTemplate<String, Object> redisTemplate) {
        this.docRepo = docRepo;
        this.fileStorageService = fileStorageService;
        this.redisTemplate = redisTemplate;
    }

    public List<Document> listByKnowledgeBase(UUID knowledgeBaseId) {
        return docRepo.findByKnowledgeBaseId(knowledgeBaseId);
    }

    @Transactional
    public Document uploadDocument(UUID knowledgeBaseId, MultipartFile file) throws IOException {
        // Validate file type
        String ext = fileStorageService.getFileExtension(file.getOriginalFilename());
        if (!ALLOWED_EXTENSIONS.contains(ext)) {
            throw new IllegalArgumentException("Unsupported file type: " + ext + ". Allowed: txt, md, pdf, doc, docx");
        }

        // Store file to local filesystem
        String filePath = fileStorageService.storeFile(knowledgeBaseId, file);

        // Create document record
        Document doc = new Document(
            knowledgeBaseId,
            file.getOriginalFilename(),
            filePath,
            ext,
            file.getSize()
        );
        Document saved = docRepo.save(doc);

        // Send Redis Stream message to notify Python document processor
        sendProcessingMessage(saved);

        return saved;
    }

    @Transactional
    public void deleteDocument(UUID knowledgeBaseId, UUID documentId) {
        Document doc = docRepo.findById(documentId)
                .orElseThrow(() -> new IllegalArgumentException("Document not found: " + documentId));

        // Verify the document belongs to the specified knowledge base
        if (!doc.getKnowledgeBaseId().equals(knowledgeBaseId)) {
            throw new IllegalArgumentException("Document does not belong to this knowledge base");
        }

        // Delete physical file
        fileStorageService.deleteFile(doc.getFilePath());

        // Delete database record (chunks will be cascade-deleted)
        docRepo.delete(doc);
    }

    /**
     * 重试处理失败的文档。
     * 将状态为 error 的文档重新发送到 Redis Stream 进行处理。
     */
    @Transactional
    public Document retryDocument(UUID knowledgeBaseId, UUID documentId) {
        Document doc = docRepo.findById(documentId)
                .orElseThrow(() -> new IllegalArgumentException("Document not found: " + documentId));

        // Verify the document belongs to the specified knowledge base
        if (!doc.getKnowledgeBaseId().equals(knowledgeBaseId)) {
            throw new IllegalArgumentException("Document does not belong to this knowledge base");
        }

        // Only allow retry for error status documents
        if (!"error".equals(doc.getStatus())) {
            throw new IllegalArgumentException("Only documents with 'error' status can be retried, current status: " + doc.getStatus());
        }

        // Reset status to uploaded for reprocessing
        doc.setStatus("uploaded");

        docRepo.save(doc);

        // Re-send Redis Stream message
        sendProcessingMessage(doc);

        return doc;
    }


    private void sendProcessingMessage(Document document) {
        try {
            Map<String, Object> messageBody = new HashMap<>();
            messageBody.put("documentId", document.getId().toString());
            messageBody.put("knowledgeBaseId", document.getKnowledgeBaseId().toString());
            messageBody.put("filePath", document.getFilePath());
            messageBody.put("fileType", document.getFileType());
            messageBody.put("fileName", document.getName());

            ObjectRecord<String, Map<String, Object>> record = StreamRecords
                    .newRecord()
                    .ofObject(messageBody)
                    .withStreamKey(REDIS_STREAM_KEY);

            redisTemplate.opsForStream().add(record);
        } catch (Exception e) {
            // Log but don't fail the upload - document is already saved
            System.err.println("Failed to send Redis Stream message: " + e.getMessage());
        }
    }
}