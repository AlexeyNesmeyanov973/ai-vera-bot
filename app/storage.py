import os
import logging
from typing import Optional, Tuple
from datetime import date, datetime
from app.config import REDIS_URL, DATABASE_URL

logger = logging.getLogger(__name__)

# ---- Redis (опционально) ----
_redis = None
if REDIS_URL:
    try:
        import redis
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        logger.info("✅ Redis подключен")
    except Exception as e:
        logger.warning(f"⚠️ Redis недоступен: {e}")
        _redis = None

# ---- Postgres (опционально) ----
_pg_conn = None
if DATABASE_URL:
    try:
        import psycopg
        _pg_conn = psycopg.connect(DATABASE_URL, autocommit=True)
        with _pg_conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
              user_id BIGINT PRIMARY KEY,
              used_seconds INTEGER NOT NULL DEFAULT 0,
              last_reset_date DATE NOT NULL
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pro_users (
              user_id BIGINT PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
        logger.info("✅ Postgres подключен, таблицы готовы")
    except Exception as e:
        logger.warning(f"⚠️ Postgres недоступен: {e}")
        _pg_conn = None

# ---- Memory fallback ----
_mem_usage = {}  # user_id -> (used_seconds:int, last_reset_date:date)
_mem_pro = set()

# ======= API: Usage (лимиты) =======

def get_usage(user_id: int) -> Tuple[int, date]:
    # Redis сначала
    if _redis:
        key = f"usage:{user_id}"
        pip = _redis.pipeline()
        pip.hget(key, "used_seconds")
        pip.hget(key, "last_reset_date")
        used_s, last = pip.execute()
        if used_s is not None and last is not None:
            try:
                return int(used_s), date.fromisoformat(last)
            except Exception:
                pass
    # Postgres
    if _pg_conn:
        with _pg_conn.cursor() as cur:
            cur.execute("SELECT used_seconds, last_reset_date FROM user_usage WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row:
                return row[0], row[1]
    # Memory
    if user_id in _mem_usage:
        return _mem_usage[user_id]
    # default
    today = date.today()
    return 0, today

def set_usage(user_id: int, used_seconds: int, last_reset_date: date):
    # Redis
    if _redis:
        key = f"usage:{user_id}"
        _redis.hset(key, mapping={"used_seconds": used_seconds, "last_reset_date": last_reset_date.isoformat()})
        _redis.expire(key, 60 * 60 * 24 * 3)  # TTL 3 дня
    # Postgres
    if _pg_conn:
        with _pg_conn.cursor() as cur:
            cur.execute("""
            INSERT INTO user_usage (user_id, used_seconds, last_reset_date)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET used_seconds=EXCLUDED.used_seconds,
                                               last_reset_date=EXCLUDED.last_reset_date
            """, (user_id, used_seconds, last_reset_date))
    # Memory
    _mem_usage[user_id] = (used_seconds, last_reset_date)

# ======= API: PRO =======

def is_pro(user_id: int) -> bool:
    # Redis
    if _redis:
        return _redis.sismember("pro_users", user_id)
    # Postgres
    if _pg_conn:
        with _pg_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pro_users WHERE user_id=%s", (user_id,))
            return cur.fetchone() is not None
    # Memory
    return user_id in _mem_pro

def add_pro(user_id: int):
    if _redis:
        _redis.sadd("pro_users", user_id)
    if _pg_conn:
        with _pg_conn.cursor() as cur:
            cur.execute("INSERT INTO pro_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    _mem_pro.add(user_id)

def remove_pro(user_id: int):
    if _redis:
        _redis.srem("pro_users", user_id)
    if _pg_conn:
        with _pg_conn.cursor() as cur:
            cur.execute("DELETE FROM pro_users WHERE user_id=%s", (user_id,))
    _mem_pro.discard(user_id)

def count_pro() -> int:
    if _redis:
        try:
            return _redis.scard("pro_users")
        except Exception:
            pass
    if _pg_conn:
        with _pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(1) FROM pro_users")
            (cnt,) = cur.fetchone()
            return int(cnt)
    return len(_mem_pro)
