-- 文档目录状态字段：给 documents 表增加 toc_status 字段
-- 用于独立跟踪文档目录（TOC）的生成状态，与文档入库状态（status）解耦
-- 取值: pending（等待生成）, processing（生成中）, completed（已生成）, error（生成失败）
ALTER TABLE documents ADD COLUMN IF NOT EXISTS toc_status VARCHAR(50) DEFAULT 'pending';
