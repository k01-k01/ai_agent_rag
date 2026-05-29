package com.rag.chat.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class ChatRequest {
    private String message;

    @JsonProperty("knowledge_base_id")
    private String knowledgeBaseId;

    @JsonProperty("conversation_id")
    private String conversationId;

    public ChatRequest() {
    }

    public ChatRequest(String message, String knowledgeBaseId, String conversationId) {
        this.message = message;
        this.knowledgeBaseId = knowledgeBaseId;
        this.conversationId = conversationId;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public String getKnowledgeBaseId() {
        return knowledgeBaseId;
    }

    public void setKnowledgeBaseId(String knowledgeBaseId) {
        this.knowledgeBaseId = knowledgeBaseId;
    }

    public String getConversationId() {
        return conversationId;
    }

    public void setConversationId(String conversationId) {
        this.conversationId = conversationId;
    }
}
