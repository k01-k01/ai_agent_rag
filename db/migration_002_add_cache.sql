-- 缓存条目表（用于二级语义缓存）
CREATE TABLE IF NOT EXISTS cache_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    agent_type VARCHAR(10) NOT NULL DEFAULT 'chat',  -- 'rag' 或 'chat'
    embedding vector(1024),
    created_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP DEFAULT NOW() + INTERVAL '24 hours'
);

-- HNSW 向量索引（用于语义相似度检索）
CREATE INDEX IF NOT EXISTS cache_entries_embedding_idx ON cache_entries USING hnsw (embedding vector_cosine_ops);

-- 清理过期缓存条目的函数
CREATE OR REPLACE FUNCTION cleanup_expired_cache_entries() RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM cache_entries WHERE expires_at < NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 定期清理触发器（每次插入后清理过期条目）
DROP TRIGGER IF EXISTS trigger_cleanup_expired_cache ON cache_entries;
CREATE TRIGGER trigger_cleanup_expired_cache
    AFTER INSERT ON cache_entries
    FOR EACH STATEMENT
    EXECUTE FUNCTION cleanup_expired_cache_entries();
