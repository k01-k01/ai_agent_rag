"""
Datetime Tool - 获取当前日期时间工具
供 Agent 调用，获取当前的日期、时间、星期等信息。
"""
import logging
from datetime import datetime

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def get_current_datetime() -> str:
    """
    获取当前的日期和时间信息。
    当用户询问"今天几号"、"现在几点"、"今天是星期几"等与当前时间相关的问题时使用此工具。
    
    Returns:
        当前日期时间信息，包含年、月、日、星期、时间
    """
    now = datetime.now()
    
    # 星期几的中文映射
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday_str = weekdays[now.weekday()]
    
    result = (
        f"当前时间：{now.year}年{now.month}月{now.day}日 "
        f"{weekday_str} "
        f"{now.hour:02d}:{now.minute:02d}:{now.second:02d}"
    )
    
    logger.info(f"Tool 'get_current_datetime' called: {result}")
    return result
