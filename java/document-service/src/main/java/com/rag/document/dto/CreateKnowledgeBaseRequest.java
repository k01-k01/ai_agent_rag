package com.rag.document.dto;

public class CreateKnowledgeBaseRequest {
    private String name;

    public CreateKnowledgeBaseRequest() {}

    public CreateKnowledgeBaseRequest(String name) {
        this.name = name;
    }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
}