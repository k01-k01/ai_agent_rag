-- 文档摘要功能：给 documents 表增加 summary 字段
-- 用于存储文档入库时自动生成的摘要（3-5个要点）
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary TEXT;
