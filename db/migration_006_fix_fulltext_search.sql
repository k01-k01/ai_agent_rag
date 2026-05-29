-- 全文检索修复：改用 pg_trgm 替代 simple 分词器
-- simple 分词器无法正确处理中文，导致全文检索始终返回 0 结果
-- pg_trgm 基于三元组匹配，天然支持中文

-- 1. 启用 pg_trgm 扩展
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 2. 创建 pg_trgm GIN 索引（基于 content 列）
CREATE INDEX IF NOT EXISTS chunks_content_trgm_idx ON chunks USING gin (content gin_trgm_ops);

-- 3. 删除旧的 TSVECTOR 相关对象（不再使用）
DROP TRIGGER IF EXISTS chunks_search_vector_trigger ON chunks;
DROP FUNCTION IF EXISTS chunks_search_vector_update();
DROP INDEX IF EXISTS chunks_search_vector_idx;

-- 4. 删除 search_vector 列（不再需要）
ALTER TABLE chunks DROP COLUMN IF EXISTS search_vector;
