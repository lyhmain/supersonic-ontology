"""配置模块：集中管理所有中间件与模型配置，支持环境变量覆盖"""

from kb_rag.config.config import get_config

__all__ = ["get_config"]
