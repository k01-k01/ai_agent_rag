package com.rag.document.repository;

import com.rag.document.model.Document;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface DocumentRepository extends JpaRepository<Document, UUID> {
    List<Document> findByKnowledgeBaseId(UUID knowledgeBaseId);

    /**
     * Batch delete documents by knowledge base ID using a single JPQL query.
     * Avoids N+1 select-then-delete behavior of the default Spring Data JPA implementation.
     */
    @Modifying
    @Query("DELETE FROM Document d WHERE d.knowledgeBaseId = :knowledgeBaseId")
    void deleteByKnowledgeBaseId(@Param("knowledgeBaseId") UUID knowledgeBaseId);
}
