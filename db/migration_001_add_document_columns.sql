-- 为已存在的 documents 表补充缺失的列（用于已有数据库的迁移）
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS file_path VARCHAR(500),
    ADD COLUMN IF NOT EXISTS file_type VARCHAR(20),
    ADD COLUMN IF NOT EXISTS file_size BIGINT;
