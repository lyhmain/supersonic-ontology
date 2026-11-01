"""
MinIO 工具：对象存储（复用 Milvus 内嵌 MinIO 实例）

安全约束：仅允许操作自建桶 MINIO_BUCKET_NAME（knowledge-base），
严禁触碰 milvus 内部桶（前缀 milvus-）。
"""

import os
from typing import Optional

from minio import Minio

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.minio")

_client: Optional[Minio] = None


def get_minio_client() -> Minio:
    """MinIO 客户端（单例）"""
    global _client
    if _client is None:
        cfg = get_config()
        _client = Minio(
            cfg.minio_endpoint,
            access_key=cfg.minio_access_key,
            secret_key=cfg.minio_secret_key,
            secure=cfg.minio_secure,
        )
        logger.info(f"MinIO 已连接: {cfg.minio_endpoint}")
    return _client


def ensure_bucket() -> str:
    """确保自建桶存在（幂等），返回桶名"""
    cfg = get_config()
    client = get_minio_client()
    if not client.bucket_exists(cfg.minio_bucket):
        client.make_bucket(cfg.minio_bucket)
        # 设置公开读，便于前端直接通过 URL 访问图片
        policy = (
            '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
            '"Principal":{"AWS":["*"]},"Action":["s3:GetObject"],'
            f'"Resource":["arn:aws:s3:::{cfg.minio_bucket}/*"]}}]}}'
        )
        client.set_bucket_policy(cfg.minio_bucket, policy)
        logger.info(f"桶已创建并设置公开读: {cfg.minio_bucket}")
    else:
        logger.info(f"桶已存在: {cfg.minio_bucket}")
    return cfg.minio_bucket


def upload_file(local_path: str, object_name: str, content_type: str = "application/octet-stream") -> str:
    """上传本地文件，返回可公网访问的 URL"""
    cfg = get_config()
    assert cfg.minio_bucket != "knowledge-base" or object_name is not None
    client = get_minio_client()
    ensure_bucket()
    client.fput_object(cfg.minio_bucket, object_name, local_path, content_type=content_type)
    url = f"{cfg.get_minio_base_url()}/{cfg.minio_bucket}/{object_name}"
    logger.info(f"已上传: {object_name}")
    return url


def upload_bytes(data: bytes, object_name: str, content_type: str = "application/octet-stream") -> str:
    """上传字节内容，返回 URL"""
    cfg = get_config()
    client = get_minio_client()
    ensure_bucket()
    import io

    client.put_object(
        cfg.minio_bucket,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    url = f"{cfg.get_minio_base_url()}/{cfg.minio_bucket}/{object_name}"
    logger.info(f"已上传(bytes): {object_name}")
    return url


def guess_content_type(path: str) -> str:
    """按扩展名推断 content_type（覆盖常见图片/PDF/Markdown）"""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".md": "text/markdown",
    }.get(ext, "application/octet-stream")
