import os

# 数据库配置
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "rag_kb")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "2wsxcde3")

# Redis配置
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# ===== DeepSeek 配置 =====
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 嵌入模型路径（BGE-M3 本地模型）
BGE_M3_MODEL_PATH = os.getenv(
    "BGE_M3_MODEL_PATH",
    "D:\\project\\models\\bge-m3"
)
RERANKER_MODEL_PATH = os.getenv(
    "RERANKER_MODEL_PATH",
    "D:\\project\\models\\bge-reranker-v2-m3"
)

# Java服务地址
JAVA_CHAT_SERVICE_URL = os.getenv("JAVA_CHAT_SERVICE_URL", "http://localhost:8082")

# 相似度阈值
CACHE_SIMILARITY_THRESHOLD = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.95"))

# 数据库连接字符串
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def get_current_llm_config() -> dict:
    """
    获取当前 LLM 的配置（固定使用 DeepSeek）。
    
    Returns:
        dict: {
            "provider": "deepseek",
            "api_key": str,
            "api_base": str,
            "model": str,
        }
    """
    return {
        "provider": "deepseek",
        "api_key": DEEPSEEK_API_KEY,
        "api_base": DEEPSEEK_API_BASE,
        "model": DEEPSEEK_MODEL,
    }


def update_llm_config(api_key: str = None, api_base: str = None, model: str = None) -> dict:
    """
    更新 DeepSeek 的 LLM 配置（运行时更新全局变量，并持久化到 .env 文件）。
    
    Args:
        api_key: 新的 API Key（可选，传 None 表示不修改）
        api_base: 新的 API Base URL（可选）
        model: 新的 Model 名称（可选）
    
    Returns:
        更新后的配置 dict
    """
    global DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL
    
    if api_key is not None:
        DEEPSEEK_API_KEY = api_key
    if api_base is not None:
        DEEPSEEK_API_BASE = api_base
    if model is not None:
        DEEPSEEK_MODEL = model
    
    # 持久化到 .env 文件
    _save_to_env(api_key, api_base, model)
    
    return {
        "provider": "deepseek",
        "api_key": DEEPSEEK_API_KEY,
        "api_base": DEEPSEEK_API_BASE,
        "model": DEEPSEEK_MODEL,
    }


def _save_to_env(api_key: str = None, api_base: str = None, model: str = None):
    """
    将 DeepSeek 配置持久化写入 .env 文件，确保重启后配置仍然保留。
    """
    import os
    # .env 文件位于项目根目录（与 config.py 同级的 .env）
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
    env_path = os.path.normpath(env_path)
    
    # 读取当前 .env 内容
    lines = []
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    
    # 构建要更新的键值对
    updates = {}
    if api_key is not None:
        updates["DEEPSEEK_API_KEY"] = api_key
    if api_base is not None:
        updates["DEEPSEEK_API_BASE"] = api_base
    if model is not None:
        updates["DEEPSEEK_MODEL"] = model
    
    if not updates:
        return
    
    # 更新或追加
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if '=' in stripped and not stripped.startswith('#'):
            key = stripped.split('=', 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)
    
    # 追加未找到的键
    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")
    
    with open(env_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
