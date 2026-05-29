-- 为 cache_entries 表添加 knowledge_base_id 列
-- 用于二级缓存按知识库过滤，避免跨知识库返回错误的缓存答案
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cache_entries' AND column_name = 'knowledge_base_id'
    ) THEN
        ALTER TABLE cache_entries ADD COLUMN knowledge_base_id UUID;
    END IF;
END $$;

-- 创建索引以加速按知识库过滤的缓存查询
CREATE INDEX IF NOT EXISTS cache_entries_kb_id_idx ON cache_entries (knowledge_base_id);
