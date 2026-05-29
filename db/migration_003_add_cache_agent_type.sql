-- 为 cache_entries 表添加 agent_type 列（如果还不存在）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cache_entries' AND column_name = 'agent_type'
    ) THEN
        ALTER TABLE cache_entries ADD COLUMN agent_type VARCHAR(10) NOT NULL DEFAULT 'chat';
    END IF;
END $$;

-- 清理旧的脏数据：删除 question 包含 URL 编码特征（'%' 符号）的缓存条目
DELETE FROM cache_entries WHERE question LIKE '%%%';
