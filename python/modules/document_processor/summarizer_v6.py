"""
文档目录提取模块 v7 - 从文档提取目录结构（健壮版）

核心改进（相对于 v6）：
1. 修复 _infer_level_from_context 函数体未实现的 Bug，实现真正的上下文层级推断
2. 修复 setext 标题与 Atx 标题重复提取的 Bug
3. 修复代码块围栏字符检测不严谨的 Bug
4. 清理死代码（yaml_started、has_toc_page、consecutive_headings、avg_line_len 等）
5. 增强 _smooth_levels 逻辑，补充"同级但编号段数更多则 level+1"的规则
6. 修复 DOCX style name 解析（用正则提取数字，避免 "Heading 1 Char" 格式失败）
7. 增强 Markdown 缩进代码块跳过（使用 CODE_INDENT_PATTERN）
8. _detect_toc_page 增加自适应行数
9. generate_document_guide 透传 build_tree 参数
10. 增加内联代码跳过，避免 `# 标题` 被误识别

v7+ 额外修复：
- 修复代码块围栏关闭检测（关闭行必须仅含围栏字符+可选空格）
- 修复缩进代码块检测（避免列表项被误判）
- 修复 Setext 标题检测中多余的条件
- 修复 _smooth_levels 中同级编号段数相等时的层级处理
- 修复超长编号截断问题（支持任意段数编号）
- 修复 _infer_level_from_numbering 对 "1." 的歧义处理
- 增强 DOCX 样式名兼容（支持 Heading1、1 Heading、标题 1 等变体）

v8 修复（当前版本）：
- 修复代码块围栏关闭检测：关闭字符必须与开启字符一致（` ``` ` 不能关闭 `~~~`）
- 修复缩进代码块检测：文档第一行不能是缩进代码块（缺少前导空行）
- 修复内联代码跳过：使用正则匹配 `code` 或 ``code`` 模式，避免误跳复杂情况
- 修复 _smooth_levels 中 inferred 被后续逻辑错误覆盖的问题
- 修复 _smooth_levels 中无编号标题（curr_segments==0）不做调整的问题
- 移除死代码：_infer_level_from_context、_detect_toc_page
- 保留 _clean_title 供外部调用（文档注释说明）

输出格式：
{
    "toc": [
        {"level": 1, "title": "第一章 概述"},
        {"level": 2, "title": "1.1 背景"},
        {"level": 3, "title": "1.1.1 什么是RAG"}
    ],
    "toc_tree": [
        {
            "level": 1, "title": "第一章 概述",
            "children": [
                {
                    "level": 2, "title": "1.1 背景",
                    "children": [
                        {"level": 3, "title": "1.1.1 什么是RAG", "children": []}
                    ]
                }
            ]
        }
    ]
}
"""
import json
import logging
import re
from typing import List, Optional, Set, Tuple, Dict, Any

logger = logging.getLogger(__name__)

# ============================================================
# 章节标题正则模式（按优先级排序）
# ============================================================

# 常见章节标题模式
# 元组格式：(pattern, default_level)
# default_level: 当无法从编号推断层级时的默认值
HEADING_PATTERNS: List[Tuple[str, int]] = [
    # Markdown # 标题（level 由 # 数量决定，此处占位）
    (r'^#{1,6}\s+(.+)$', 0),
    # 中文章节：第一章、第一篇、第一节
    (r'^第[一二三四五六七八九十○零百千两]+[章节篇部节卷集][篇部节卷集]?\s*[:：]?\s*(.+)$', 1),
    # 数字章节：第1章、第2节
    (r'^第\s*\d+\s*[章节篇部节卷集][篇部节卷集]?\s*[:：]?\s*(.+)$', 1),
    # 第1章.概述
    (r'^第\s*\d+\s*[章节篇部节卷集][篇部节卷集]?\s*\.\s*(.+)$', 1),
    # 附录A
    (r'^(附录|附则|附件)\s*[A-Z\d]+[\s:：]*(.+)$', 1),
    # 带编号的标题：1.1、1.1.1、2.3.4.5
    # 使用更通用的模式匹配任意段数的编号（如 1.2.3.4.5）
    (r'^(\d+(?:\.\d+)+)[\s.、:：]*(.+)$', 0),  # level 由编号段数决定
    # 数字编号：1.  2、  3：
    (r'^\d+[.、:：]\s+(.+)$', 2),
    # 中文数字编号：一、二、三、
    (r'^[一二三四五六七八九十]+[、,，]\s*(.+)$', 1),
    # 中文数字编号：（一）、（二）
    (r'^[（(][一二三四五六七八九十]+[）)]\s+(.+)$', 2),
    # 阿拉伯数字括号：（1）(2)
    (r'^[（(]\d+[）)]\s+(.+)$', 3),
]

# 需要从编号段数推断层级的模式索引
INFER_LEVEL_PATTERNS = {3}  # 索引3是 `(\d+(?:\.\d+)+)` 模式

# ============================================================
# Markdown 特殊处理
# ============================================================

# YAML front matter 标记
YAML_FRONT_MATTER_PATTERN = re.compile(r'^---\s*$')

# 代码块标记
CODE_FENCE_PATTERN = re.compile(r'^(```|~~~)')
CODE_INDENT_PATTERN = re.compile(r'^(    |\t)')  # 4空格或tab缩进

# 引用块标记
BLOCKQUOTE_PATTERN = re.compile(r'^>\s?')

# Setext 标题标记（下方用 === 或 --- 的行）
SETEXT_H1_PATTERN = re.compile(r'^={3,}\s*$')
SETEXT_H2_PATTERN = re.compile(r'^-{3,}\s*$')


# ============================================================
# 1. Markdown 目录提取（增强版）
# ============================================================

def _extract_md_toc(content: str) -> List[dict]:
    """
    从 Markdown 内容提取目录结构（增强版）。

    改进：
    - 跳过代码块内的 #（围栏式 + 缩进式）
    - 跳过引用块内的 #
    - 跳过 YAML front matter
    - 跳过内联代码行
    - 支持 setext 风格标题
    - 修复 setext 与 Atx 标题重复提取问题
    """
    toc = []
    lines = content.split('\n')

    in_code_block = False
    in_yaml_front_matter = False
    in_indented_code_block = False
    code_fence_char = ''

    # 用于 setext 标题：记录上一行
    prev_line = ''
    prev_was_atx_heading = False  # 标记上一行是否已被 Atx 逻辑提取

    for i, line in enumerate(lines):
        line_stripped = line.strip()

        # --- 状态跟踪 ---

        # YAML front matter
        if i == 0 and YAML_FRONT_MATTER_PATTERN.match(line_stripped):
            in_yaml_front_matter = True
            continue
        if in_yaml_front_matter:
            if YAML_FRONT_MATTER_PATTERN.match(line_stripped):
                in_yaml_front_matter = False
            continue

        # 代码块（围栏式）
        fence_match = CODE_FENCE_PATTERN.match(line_stripped)
        if fence_match:
            if not in_code_block:
                in_code_block = True
                code_fence_char = fence_match.group(1)
            elif code_fence_char and line_stripped.startswith(code_fence_char):
                # 关闭行必须与开启字符一致，且仅包含围栏字符（3个以上）和可选空格
                close_pattern = re.compile(r'^' + re.escape(code_fence_char) + r'{3,}\s*$')
                if close_pattern.match(line_stripped):
                    in_code_block = False
            continue

        if in_code_block:
            continue

        # 代码块（缩进式）：4空格或tab开头，且前后有空行分隔
        # 注意：仅当上一行是空行且下一行也是缩进或空行时才判定为缩进代码块
        # 避免将列表项、嵌套列表等误判为代码块
        if not in_indented_code_block and CODE_INDENT_PATTERN.match(line):
            # 文档第一行不能是缩进代码块（缺少前导空行）
            prev_is_blank = (i > 0 and not lines[i - 1].strip())
            next_is_indented_or_blank = (
                i + 1 >= len(lines) or
                not lines[i + 1].strip() or
                CODE_INDENT_PATTERN.match(lines[i + 1])
            )
            if prev_is_blank and next_is_indented_or_blank:
                in_indented_code_block = True
                continue
        if in_indented_code_block:
            if CODE_INDENT_PATTERN.match(line):
                continue
            else:
                in_indented_code_block = False

        # 引用块
        if BLOCKQUOTE_PATTERN.match(line_stripped):
            continue

        # 跳过内联代码行（整行被单个反引号或双反引号包裹的纯代码行）
        # 匹配 `code` 或 ``code`` 模式，避免误跳 `hello`world` 这类复杂情况
        inline_code_match = re.match(r'^`{1,2}(.+?)`{1,2}$', line_stripped)
        if inline_code_match and len(line_stripped) >= 3:
            prev_line = line_stripped
            prev_was_atx_heading = False
            continue

        # --- 标题提取 ---

        # 1) Atx 风格标题 (#)
        match = re.match(r'^(#{1,6})\s+(.+)$', line_stripped)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if title:
                toc.append({"level": level, "title": title})
            prev_line = line_stripped
            prev_was_atx_heading = True
            continue

        # 2) Setext 风格标题（上一行是文本，当前行是 === 或 ---）
        # 注意：仅当上一行不是 Atx 标题时才检测，避免重复提取
        if i > 0 and prev_line and not prev_was_atx_heading:
            prev_stripped = prev_line.strip()
            if SETEXT_H1_PATTERN.match(line_stripped) and prev_stripped:
                # 上一行是 H1
                if prev_stripped and len(prev_stripped) >= 2:
                    toc.append({"level": 1, "title": prev_stripped})
                prev_line = line_stripped
                prev_was_atx_heading = False
                continue
            elif SETEXT_H2_PATTERN.match(line_stripped) and prev_stripped:
                # 上一行是 H2
                if prev_stripped and len(prev_stripped) >= 2:
                    toc.append({"level": 2, "title": prev_stripped})
                prev_line = line_stripped
                prev_was_atx_heading = False
                continue

        prev_line = line_stripped
        prev_was_atx_heading = False

    logger.info(f"Extracted {len(toc)} headings from Markdown (enhanced)")
    return toc


# ============================================================
# 2. Word DOCX 目录提取
# ============================================================

def _extract_docx_toc(file_path: str, content: str = '') -> List[dict]:
    """
    从 Word 文档提取目录结构。

    改进：
    - 如果 python-docx 不可用，回退到通用提取
    - 如果样式提取结果为空，回退到通用提取
    - 用正则提取 Heading 层级数字，兼容 "Heading 1 Char" 等变体样式名
    """
    try:
        from docx import Document
    except ImportError:
        logger.warning("python-docx not installed, falling back to generic extraction")
        return _extract_generic_toc(content) if content else []

    toc = []
    try:
        doc = Document(file_path)
        for para in doc.paragraphs:
            style_name = para.style.name
            # 兼容多种 Heading 样式名变体：
            # "Heading 1", "Heading 1 Char", "Heading1", "1 Heading", "标题 1"
            level_match = re.search(r'(?:Heading|标题)\s*(\d+)', style_name, re.IGNORECASE)
            if level_match:
                level = int(level_match.group(1))
                title = para.text.strip()
                if title:
                    toc.append({"level": level, "title": title})

        # 如果样式提取结果为空，回退到通用提取
        if not toc and content:
            logger.info("DOCX style extraction returned empty, falling back to generic")
            return _extract_generic_toc(content)

    except Exception as e:
        logger.error(f"Failed to extract TOC from DOCX: {e}")
        if content:
            return _extract_generic_toc(content)

    logger.info(f"Extracted {len(toc)} headings from DOCX")
    return toc


# ============================================================
# 3. 层级推断工具函数
# ============================================================

def _infer_level_from_numbering(title: str) -> Optional[int]:
    """
    根据编号模式推断标题层级。

    规则：
    - "1.1" → level 2
    - "1.1.1" → level 3
    - "第一章" → level 1
    - "第1章" → level 1
    - "附录A" → level 1
    - 无编号 → None（需用默认值）
    """
    # 匹配 "1.1"、"1.1.1"、"1.2.3.4.5" 模式（任意段数）
    m = re.match(r'^(\d+(?:\.\d+)+)[\s.、:：]', title)
    if m:
        return m.group(1).count('.') + 1

    # 匹配 "1."、"2、" 模式
    # 注意：返回 None 而非 1，让调用方结合上下文决定层级
    # 因为 "1." 可能是 level 1（如 "1. 引言"），也可能是 level 2（如前面有 "第一章"）
    m = re.match(r'^\d+[.、:：]', title)
    if m:
        return None

    # 匹配 "第一章"、"第1章"
    if re.match(r'^第[一二三四五六七八九十○零百千两\d]+[章节篇部节卷集]', title):
        return 1

    # 匹配 "附录A"
    if re.match(r'^(附录|附则|附件)', title):
        return 1

    # 匹配 "一、"、"二、"（中文数字+顿号/逗号）
    if re.match(r'^[一二三四五六七八九十]+[、,，]', title):
        return 1

    # 匹配 "（一）"、"（1）"
    if re.match(r'^[（(][一二三四五六七八九十\d]+[）)]', title):
        return 2

    # 匹配 "A."、"a)"
    if re.match(r'^[A-Za-z][)）.]', title):
        return 3

    return None


def _count_numbering_segments(title: str) -> int:
    """计算标题中编号的段数，如 '1.1.1' 返回 3，'1.1' 返回 2。"""
    m = re.match(r'^(\d+(?:\.\d+)+)', title)
    if m:
        return m.group(1).count('.') + 1
    return 0


# ============================================================
# 4. 通用目录提取（PDF/TXT）- 优化版
# ============================================================

def _is_likely_heading(
    line: str,
    prev_line_was_heading: bool,
    next_line: Optional[str] = None,
    prev_line: Optional[str] = None,
) -> Tuple[bool, int]:
    """
    判断一行文本是否可能是标题（优化版）。

    优化说明：
    - 仅依赖正则匹配 HEADING_PATTERNS 来识别标题
    - 移除了上下文特征检测兜底逻辑，避免正文短行被误判为标题
    - PDF 中的标题格式是固定的（大写数字、小写数字、（数字）、1.1、1.1.1），
      正则匹配已足够覆盖，不需要额外的启发式判断

    Args:
        line: 文本行
        prev_line_was_heading: 前一行是否是标题
        next_line: 下一行文本（用于检测空行分隔）
        prev_line: 上一行文本（用于检测空行分隔）

    Returns:
        (是否标题, 层级估算)
    """
    line_stripped = line.strip()
    if not line_stripped:
        return False, 0

    # 跳过过长或过短的行
    if len(line_stripped) > 150 or len(line_stripped) < 2:
        return False, 0

    # 尝试从编号推断层级
    inferred_level = _infer_level_from_numbering(line_stripped)

    # 【修复】先尝试匹配正则模式，再执行标点检查
    # 避免中文标题（如"一、数据清洗：通用方法（核心：去噪、规整、提效）"）
    # 因包含较多中文标点而被误判为正文句子
    for pattern_idx, (pattern, default_level) in enumerate(HEADING_PATTERNS):
        match = re.match(pattern, line_stripped)
        if match:
            # Markdown 标题：level 由 # 数量决定
            if pattern == r'^#{1,6}\s+(.+)$':
                level = len(match.group(1))
                return True, level

            # 带编号的层级模式（如 1.1、1.1.1）：从编号段数推断
            if pattern_idx in INFER_LEVEL_PATTERNS:
                if inferred_level is not None:
                    return True, inferred_level
                # 从 match groups 推断
                groups = [g for g in match.groups() if g is not None]
                # 最后一个 group 是标题文本，前面的都是编号段
                num_groups = len(groups) - 1
                if num_groups >= 1:
                    return True, num_groups
                return True, default_level

            # 其他模式：优先使用推断层级
            if inferred_level is not None:
                return True, inferred_level
            return True, default_level

    # 【兜底检查】仅对未匹配正则的行进行过滤，防止正文短行被误判为标题
    # 跳过包含太多中文标点的行（可能是正文句子）
    chinese_punct = sum(1 for c in line_stripped if c in '。，、；：""''（）【】《》？！…—')
    if len(line_stripped) > 20 and chinese_punct / len(line_stripped) > 0.35:
        return False, 0

    # 跳过以句号/问号/感叹号结尾的行（通常是完整句子）
    if line_stripped[-1] in '。？！?！':
        return False, 0

    # 【优化】不再使用上下文特征检测兜底
    # PDF 中的标题格式是固定的，正则匹配已足够覆盖
    # 上下文特征检测会引入大量误匹配（如短行、数字行、列表项等）
    return False, 0


def _extract_generic_toc(content: str) -> List[dict]:
    """
    从通用文本（PDF/TXT）提取目录结构（优化版）。

    优化说明：
    - 仅依赖正则匹配识别标题，不再使用上下文特征检测兜底
    - 保留标题中的编号前缀，输出完整标题
    - 改进去重逻辑，考虑层级信息
    """
    lines = content.split('\n')
    if not lines:
        return []

    toc = []
    prev_line_was_heading = False

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            prev_line_was_heading = False
            continue

        # 获取上下文
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        prev_line = lines[i - 1] if i > 0 else None

        is_heading, level = _is_likely_heading(
            line_stripped,
            prev_line_was_heading,
            next_line=next_line,
            prev_line=prev_line,
        )

        if is_heading:
            # 【优化】保留完整标题，不再清理编号前缀
            # 这样输出的标题与 PDF 中的标题完全一致
            title = line_stripped

            if title and len(title) >= 2:
                # 避免连续重复标题
                if not toc or toc[-1]["title"] != title:
                    # 尝试从编号重新推断层级（更精确）
                    inferred = _infer_level_from_numbering(line_stripped)
                    if inferred is not None:
                        level = inferred

                    toc.append({"level": level, "title": title})

            prev_line_was_heading = True
        else:
            prev_line_was_heading = False

    # 后处理：平滑层级（确保层级递增合理）
    toc = _smooth_levels(toc)

    logger.info(f"Extracted {len(toc)} headings via generic extraction (optimized)")
    return toc


def _clean_title(title: str) -> str:
    """
    清理标题文本，移除序号前缀。

    注意：此函数当前未被 _extract_generic_toc 使用，
    因为优化版保留完整标题以保持与 PDF 原文一致。
    保留此函数供其他场景使用。
    """
    original = title

    # 移除 Markdown # 标记
    title = re.sub(r'^#{1,6}\s*', '', title)

    # 移除 "第X章" 前缀（保留后面的文本）
    title = re.sub(r'^第[一二三四五六七八九十○零百千两\d]+[章节篇部节卷集][篇部节卷集]?\s*[:：]?\s*', '', title)

    # 移除 "第X章." 前缀
    title = re.sub(r'^第\s*\d+\s*[章节篇部节卷集][篇部节卷集]?\s*\.\s*', '', title)

    # 移除 "附录A" 前缀
    title = re.sub(r'^(附录|附则|附件)\s*[A-Z\d]*[\s:：]*', '', title)

    # 移除 "1.1.1" 编号前缀
    title = re.sub(r'^(\d+(?:\.\d+)+)[\s.、:：]+', '', title)

    # 移除 "1." "2、" "3：" 编号前缀
    title = re.sub(r'^\d+[.、:：]\s*', '', title)

    # 移除 "（1）" "(2)" 编号前缀
    title = re.sub(r'^[（(]\d+[）)]\s*', '', title)

    # 移除 "（一）" "(二)" 编号前缀
    title = re.sub(r'^[（(][一二三四五六七八九十]+[）)]\s*', '', title)

    # 移除 "A." "a)" 编号前缀
    title = re.sub(r'^[A-Za-z][)）.]\s*', '', title)

    # 移除罗马数字前缀
    title = re.sub(r'^[ivxIVX]+[.、]?\s*', '', title)

    title = title.strip()

    # 如果清理后为空，返回原标题（可能是纯编号行）
    if not title:
        return original.strip()

    return title


def _smooth_levels(toc: List[dict]) -> List[dict]:
    """
    平滑层级，确保层级递增合理。

    问题：某些标题的层级可能被错误分配（例如 1.1 被分配为 level 1，但前一个标题是 level 1）。
    策略：
    - 如果当前 level <= 前一个 level，且当前标题编号段数更多，则 level = prev_level + 1
    - 如果当前 level 比前一个 level 大 2 以上，且中间没有过渡，则调整为 prev_level + 1
    - 如果当前 level 远小于前一个 level，可能是新的大章节开始，调整为 prev_level - 1
    """
    if not toc:
        return toc

    result = [dict(toc[0])]  # 复制，避免修改原始数据

    for i in range(1, len(toc)):
        item = dict(toc[i])  # 复制，避免修改原始数据
        prev = result[-1]

        # 步骤1：尝试从编号推断层级（最精确的方式）
        inferred = _infer_level_from_numbering(item["title"])
        if inferred is not None:
            item["level"] = inferred
        else:
            # 如果当前 level 比前一个 level 大 2 以上，调整为 prev + 1
            if item["level"] > prev["level"] + 2:
                item["level"] = prev["level"] + 1
            # 如果当前 level 远小于前一个 level，可能是新的大章节开始
            elif item["level"] < prev["level"] - 2:
                item["level"] = prev["level"] - 1

        # 步骤2：基于编号段数关系进行微调
        # 只有当两个标题都有编号段数时，才用段数关系来修正层级
        curr_segments = _count_numbering_segments(item["title"])
        prev_segments = _count_numbering_segments(prev["title"])

        if curr_segments > 0 and prev_segments > 0:
            # 两个标题都有编号段数，用段数关系来修正层级
            if curr_segments > prev_segments:
                # 编号段数更多 → 应该是子层级
                if item["level"] <= prev["level"]:
                    item["level"] = prev["level"] + 1
            elif curr_segments == prev_segments:
                # 编号段数相等 → 应该是同级
                if item["level"] != prev["level"]:
                    item["level"] = prev["level"]
            else:  # curr_segments < prev_segments
                # 编号段数更少 → 应该是父层级或更高级
                expected_level = max(1, prev["level"] - (prev_segments - curr_segments))
                if item["level"] > expected_level:
                    item["level"] = expected_level
        # 如果当前标题无编号（curr_segments == 0），保持 item["level"] 不变
        # 已由 inferred（如果为 None 则走 else 分支）或默认值决定

        # 确保 level 至少为 1
        item["level"] = max(1, item["level"])

        result.append(item)

    return result


# ============================================================
# 5. 层级树构建
# ============================================================

def build_toc_tree(toc: List[dict]) -> List[dict]:
    """
    将扁平的目录列表转换为树形结构。

    Args:
        toc: 扁平目录列表 [{"level": 1, "title": "..."}, ...]

    Returns:
        树形结构 [{"level": 1, "title": "...", "children": [...]}, ...]
    """
    if not toc:
        return []

    root: List[dict] = []
    stack: List[dict] = []  # 用于跟踪当前路径

    for item in toc:
        node = {
            "level": item["level"],
            "title": item["title"],
            "children": [],
        }

        # 找到父节点
        while stack and stack[-1]["level"] >= item["level"]:
            stack.pop()

        if stack:
            # 当前节点是栈顶的子节点
            stack[-1]["children"].append(node)
        else:
            # 当前节点是根节点
            root.append(node)

        stack.append(node)

    return root


# ============================================================
# 6. 主入口函数
# ============================================================

def extract_toc(
    content: str,
    file_type: str,
    file_path: Optional[str] = None,
    build_tree: bool = True,
) -> List[dict]:
    """
    根据文件类型提取目录结构。

    Args:
        content: 文档全文内容
        file_type: 文件类型 (md, docx, pdf, txt 等)
        file_path: 文件路径（用于 DOCX 样式提取，可选）
        build_tree: 保留参数，兼容上游调用（实际未使用）

    Returns:
        目录列表：[{"level": 1, "title": "标题"}, ...]
    """
    if not content or not content.strip():
        return []

    file_type = file_type.lower()
    toc = []

    if file_type == "md":
        toc = _extract_md_toc(content)
    elif file_type in ("doc", "docx"):
        # 优先尝试从文件读取样式
        if file_path:
            toc = _extract_docx_toc(file_path, content)
        else:
            toc = _extract_generic_toc(content)
    else:
        toc = _extract_generic_toc(content)

    # 【优化】去重时考虑层级信息
    # 不同层级的相同标题不视为重复
    seen: Set[Tuple[int, str]] = set()
    deduped_toc = []
    for item in toc:
        key = (item["level"], item["title"].lower().strip())
        if key not in seen:
            seen.add(key)
            deduped_toc.append(item)

    logger.info(f"Extracted {len(deduped_toc)} TOC entries for file type '{file_type}'")
    return deduped_toc


async def generate_document_guide(
    content: str,
    file_name: str,
    file_type: str = "txt",
    file_path: Optional[str] = None,
    chunks: Optional[List[str]] = None,
    build_tree: bool = True,
) -> str:
    """
    生成文档目录结构（零 LLM 调用）。

    流程：
    1. 根据文件类型选择提取策略
    2. 提取目录结构
    3. 构建层级树
    4. 以 JSON 格式输出

    Args:
        content: 文档全文内容
        file_name: 文档文件名
        file_type: 文件类型 (md, docx, pdf, txt 等)
        file_path: 文件路径（用于 DOCX 样式提取，可选）
        chunks: 保留参数兼容（未使用）
        build_tree: 是否构建层级树（默认 True）

    Returns:
        JSON 字符串：{"toc": [...], "toc_tree": [...]}
    """
    if not content or not content.strip():
        raise ValueError("Document content is empty")

    logger.info(
        f"Generating TOC for '{file_name}' ({file_type}), "
        f"content length: {len(content)} chars"
    )

    # 提取目录
    toc = extract_toc(content, file_type, file_path, build_tree=build_tree)

    # 构建层级树
    if build_tree:
        toc_tree = build_toc_tree(toc)
    else:
        toc_tree = []

    # 构建 JSON 输出
    result = {
        "toc": toc,
        "toc_tree": toc_tree,
    }
    json_str = json.dumps(result, ensure_ascii=False, indent=2)

    logger.info(
        f"Generated TOC for '{file_name}': {len(toc)} entries, "
        f"{len(toc_tree)} root nodes, 0 LLM calls"
    )
    return json_str
