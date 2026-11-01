"""
会话历史工具：PostgreSQL（替代原版 MongoDB）

表结构 chat_history：
  id BIGSERIAL PK
  session_id VARCHAR(64)
  role VARCHAR(16)        -- user / assistant
  content TEXT
  refs JSONB              -- 引用来源元数据（assistant 消息）
  created_at TIMESTAMPTZ DEFAULT now()
"""

import json
from typing import List, Optional

import psycopg
from psycopg.rows import dict_row

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.history")


def _connect() -> psycopg.Connection:
    cfg = get_config()
    return psycopg.connect(
        host=cfg.pg_host,
        port=cfg.pg_port,
        user=cfg.pg_user,
        password=cfg.pg_password,
        dbname=cfg.pg_db,
    )


def ensure_table() -> None:
    """确保历史表存在（幂等）"""
    cfg = get_config()
    with _connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {cfg.pg_history_table} (
                id BIGSERIAL PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                role VARCHAR(16) NOT NULL,
                content TEXT NOT NULL,
                refs JSONB DEFAULT '[]'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_feedback (
                id BIGSERIAL PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                rating VARCHAR(8) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_chat_history_session "
            f"ON {cfg.pg_history_table} (session_id, created_at)"
        )
    logger.info(f"历史表已就绪: {cfg.pg_history_table}")


def add_message(session_id: str, role: str, content: str, refs: Optional[list] = None) -> None:
    """追加一条消息（refs 为答案引用来源，仅 assistant 消息使用）"""
    cfg = get_config()
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO {cfg.pg_history_table} (session_id, role, content, refs) "
            f"VALUES (%s, %s, %s, %s)",
            (session_id, role, content, json.dumps(refs or [], ensure_ascii=False)),
        )
    logger.debug(f"历史已写入: session={session_id} role={role} len={len(content)}")


def get_history(session_id: str, limit: int = 20) -> List[dict]:
    """按时间正序取某会话最近 limit 条消息"""
    cfg = get_config()
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT role, content, refs, created_at FROM {cfg.pg_history_table} "
            f"WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
            (session_id, limit),
        )
        rows = cur.fetchall()[::-1]
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return rows


def clear_history(session_id: str) -> None:
    """清空某会话历史"""
    cfg = get_config()
    with _connect() as conn:
        conn.execute(f"DELETE FROM {cfg.pg_history_table} WHERE session_id = %s", (session_id,))
    logger.info(f"历史已清空: session={session_id}")


def add_feedback(session_id: str, question: str, answer: str, rating: str) -> None:
    """记录用户反馈（up/down），回流评测集原料"""
    rating = "up" if rating == "up" else "down"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_feedback (session_id, question, answer, rating) VALUES (%s, %s, %s, %s)",
            (session_id, question, answer, rating),
        )


def list_sessions(limit: int = 50) -> List[dict]:
    """会话列表：按最近活跃排序"""
    cfg = get_config()
    with _connect() as conn:
        rows = conn.cursor(row_factory=dict_row).execute(
            f"""
            SELECT session_id,
                   COUNT(*) AS msg_count,
                   MAX(created_at) AS last_active,
                   (ARRAY_AGG(content ORDER BY created_at DESC))[1] AS last_content
            FROM {cfg.pg_history_table}
            GROUP BY session_id
            ORDER BY last_active DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "session_id": r["session_id"],
            "msg_count": r["msg_count"],
            "last_active": r["last_active"].isoformat() if r["last_active"] else "",
            "last_content": (r["last_content"] or "")[:40],
        }
        for r in rows
    ]
