-- 安装扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 知识库表
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 文档表
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'uploaded',  -- uploaded, processing, completed, error
    file_path VARCHAR(500),
    file_type VARCHAR(20),
    file_size BIGINT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 文本块表
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding vector(1024),
    search_vector TSVECTOR,  -- 全文搜索向量（用于替代 BM25）
    created_at TIMESTAMP DEFAULT NOW()
);

-- HNSW 向量索引
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);

-- 全文搜索索引 (GIN 索引)
-- 使用内置的 TSVECTOR 和 GIN 索引替代 pg_bm25
CREATE INDEX IF NOT EXISTS chunks_search_vector_idx ON chunks USING gin (search_vector);

-- 创建自动更新 search_vector 的触发器函数
CREATE OR REPLACE FUNCTION chunks_search_vector_update() RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector := setweight(to_tsvector('simple', COALESCE(NEW.content, '')), 'A');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 为已有数据设置 search_vector
UPDATE chunks SET search_vector = setweight(to_tsvector('simple', COALESCE(content, '')), 'A');

-- 创建触发器
DROP TRIGGER IF EXISTS chunks_search_vector_trigger ON chunks;
CREATE TRIGGER chunks_search_vector_trigger
    BEFORE INSERT OR UPDATE ON chunks
    FOR EACH ROW
    EXECUTE FUNCTION chunks_search_vector_update();
