-- 为 cache_entries 表添加 sources 列（JSON 格式，存储检索来源信息）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'cache_entries' AND column_name = 'sources'
    ) THEN
        ALTER TABLE cache_entries ADD COLUMN sources TEXT;
    END IF;
END $$;
