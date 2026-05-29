package com.rag.document.service;

import com.rag.document.model.KnowledgeBase;
import com.rag.document.repository.DocumentRepository;
import com.rag.document.repository.KnowledgeBaseRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Service
public class KnowledgeBaseService {

    private final KnowledgeBaseRepository kbRepo;
    private final DocumentRepository docRepo;
    private final FileStorageService fileStorageService;

    public KnowledgeBaseService(KnowledgeBaseRepository kbRepo,
                                DocumentRepository docRepo,
                                FileStorageService fileStorageService) {
        this.kbRepo = kbRepo;
        this.docRepo = docRepo;
        this.fileStorageService = fileStorageService;
    }

    public List<KnowledgeBase> listAll() {
        return kbRepo.findAll();
    }

    public Optional<KnowledgeBase> getById(UUID id) {
        return kbRepo.findById(id);
    }

    public KnowledgeBase create(String name) {
        KnowledgeBase kb = new KnowledgeBase(name);
        return kbRepo.save(kb);
    }

    public Optional<KnowledgeBase> update(UUID id, String newName) {
        return kbRepo.findById(id).map(kb -> {
            kb.setName(newName);
            return kbRepo.save(kb);
        });
    }

    @Transactional
    public void delete(UUID id) {
        // Use existsById to check existence first (lighter than findById which loads full entity)
        if (!kbRepo.existsById(id)) {
            return;
        }
        // Batch delete associated document records via single JPQL query
        docRepo.deleteByKnowledgeBaseId(id);
        // Delete physical files
        try {
            fileStorageService.deleteKnowledgeBaseFiles(id);
        } catch (Exception ignored) {
            // Log warning in production
        }
        // Delete knowledge base record by ID directly, avoiding full entity load
        kbRepo.deleteById(id);
    }
}