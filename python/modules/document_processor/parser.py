"""
文档内容解析器 - 支持 txt, md, pdf, doc/docx
"""
import os


def parse_text(file_path: str, file_type: str) -> str:
    """
    根据文件类型解析文档内容，返回纯文本字符串。
    """
    ext = file_type.lower()

    if ext in ("txt", "md"):
        return _parse_plain_text(file_path)
    elif ext == "pdf":
        return _parse_pdf(file_path)
    elif ext in ("doc", "docx"):
        return _parse_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _parse_plain_text(file_path: str) -> str:
    """解析纯文本/markdown文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _parse_pdf(file_path: str) -> str:
    """解析PDF文件，提取文本内容"""
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except ImportError:
        raise ImportError("pypdf is required for PDF parsing. Install with: pip install pypdf")


def _parse_docx(file_path: str) -> str:
    """解析Word文档(doc/docx)，提取文本内容"""
    try:
        from docx import Document

        doc = Document(file_path)
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n".join(paragraphs)
    except ImportError:
        raise ImportError("python-docx is required for Word parsing. Install with: pip install python-docx")