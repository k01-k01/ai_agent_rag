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

# DeepSeek API 配置
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
CACHE_SIMILARITY_THRESHOLD = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.85"))

# 数据库连接字符串
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
