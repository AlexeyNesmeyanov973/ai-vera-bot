# app/storage.py
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
        import redis  # type: ignore
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
        import psycopg  # type: ignore
        _pg_conn = psycopg.connect(DATABASE_URL, autocommit=True)
        with _pg_conn.cursor() as cur:
            # Базовая таблица использования базового лимита в секундах
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_usage (
              user_id BIGINT PRIMARY KEY,
              used_seconds INTEGER NOT NULL DEFAULT 0,
              last_reset_date DATE NOT NULL
            );
            """)
            # PRO пользователи
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pro_users (
              user_id BIGINT PRIMARY KEY,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            # Таблица докупленных секунд на ТЕКУЩИЕ сутки
            cur.execute("""
            CREATE TABLE IF NOT EXISTS user_overage (
              user_id BIGINT PRIMARY KEY,
              extra_seconds INTEGER NOT NULL DEFAULT 0,
              last_reset_date DATE NOT NULL
            );
            """)
        logger.info("✅ Postgres подключен, таблицы готовы")
    except Exception as e:
        logger.warning(f"⚠️ Postgres недоступен: {e}")
        _pg_conn = None

# ---- Memory fallback ----
# user_usage: сколько базовых секунд из дневного лимита уже израсходовано сегодня
_mem_usage: dict[int, Tuple[int, date]] = {}  # user_id -> (used_seconds:int, last_reset_date:date)
# pro_users: множество PRO пользователям
_mem_pro: set[int] = set()
# user_overage: докупленные секунды на сегодня
_mem_overage: dict[int, Tuple[int, date]] = {}  # user_id -> (extra_seconds:int, last_reset_date:date)

# ============================================================
#                 БАЗОВЫЙ ЛИМИТ (user_usage)
# ============================================================

def get_usage(user_id: int) -> Tuple[int, date]:
    """
    Возвращает (used_seconds, last_reset_date) по базовому лимиту.
    """
    # Redis сначала
    if _redis:
        key = f"usage:{user_id}"
        try:
            pip = _redis.pipeline()
            pip.hget(key, "used_seconds")
            pip.hget(key, "last_reset_date")
            used_s, last = pip.execute()
            if used_s is not None and last is not None:
                try:
                    return int(used_s), date.fromisoformat(last)
                except Exception:
                    pass
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT used_seconds, last_reset_date FROM user_usage WHERE user_id=%s",
                    (user_id,),
                )
                row = cur.fetchone()
                if row:
                    return int(row[0]), row[1]
        except Exception as e:
            logger.debug(f"Postgres get_usage error: {e}")

    # Memory
    if user_id in _mem_usage:
        return _mem_usage[user_id]

    # default
    today = date.today()
    return 0, today


def set_usage(user_id: int, used_seconds: int, last_reset_date: date):
    """
    Устанавливает (used_seconds, last_reset_date) по базовому лимиту.
    """
    # Redis
    if _redis:
        try:
            key = f"usage:{user_id}"
            _redis.hset(
                key,
                mapping={
                    "used_seconds": int(used_seconds),
                    "last_reset_date": last_reset_date.isoformat(),
                },
            )
            # TTL 3 дня, чтобы мусор не скапливался
            _redis.expire(key, 60 * 60 * 24 * 3)
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_usage (user_id, used_seconds, last_reset_date)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        used_seconds = EXCLUDED.used_seconds,
                        last_reset_date = EXCLUDED.last_reset_date
                    """,
                    (user_id, int(used_seconds), last_reset_date),
                )
        except Exception as e:
            logger.debug(f"Postgres set_usage error: {e}")

    # Memory
    _mem_usage[user_id] = (int(used_seconds), last_reset_date)

# ============================================================
#                        PRO СТАТУС
# ============================================================

def is_pro(user_id: int) -> bool:
    """
    Является ли пользователь PRO.
    """
    # Redis
    if _redis:
        try:
            return bool(_redis.sismember("pro_users", user_id))
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pro_users WHERE user_id=%s", (user_id,))
                return cur.fetchone() is not None
        except Exception as e:
            logger.debug(f"Postgres is_pro error: {e}")

    # Memory
    return user_id in _mem_pro


def add_pro(user_id: int):
    """
    Добавляет пользователя в PRO.
    """
    # Redis
    if _redis:
        try:
            _redis.sadd("pro_users", user_id)
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pro_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (user_id,),
                )
        except Exception as e:
            logger.debug(f"Postgres add_pro error: {e}")

    # Memory
    _mem_pro.add(user_id)


def remove_pro(user_id: int):
    """
    Удаляет пользователя из PRO.
    """
    # Redis
    if _redis:
        try:
            _redis.srem("pro_users", user_id)
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("DELETE FROM pro_users WHERE user_id=%s", (user_id,))
        except Exception as e:
            logger.debug(f"Postgres remove_pro error: {e}")

    # Memory
    _mem_pro.discard(user_id)


def count_pro() -> int:
    """
    Количество PRO пользователей (оценочно).
    """
    # Redis
    if _redis:
        try:
            return int(_redis.scard("pro_users"))
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT COUNT(1) FROM pro_users")
                (cnt,) = cur.fetchone()
                return int(cnt)
        except Exception as e:
            logger.debug(f"Postgres count_pro error: {e}")

    # Memory
    return len(_mem_pro)

# ============================================================
#                   ДОКУПКА СЕКУНД (user_overage)
# ============================================================

def get_overage(user_id: int) -> Tuple[int, date]:
    """
    Возвращает (extra_seconds, last_reset_date) для докупленных секунд.
    Если дата не совпадает с сегодняшней, воспринимаем как 0 на сегодня.
    """
    # Redis
    if _redis:
        key = f"overage:{user_id}"
        try:
            extra = _redis.hget(key, "extra_seconds")
            last = _redis.hget(key, "last_reset_date")
            if extra is not None and last is not None:
                try:
                    return int(extra), date.fromisoformat(last)
                except Exception:
                    pass
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT extra_seconds, last_reset_date FROM user_overage WHERE user_id=%s",
                    (user_id,),
                )
                row = cur.fetchone()
                if row:
                    return int(row[0]), row[1]
        except Exception as e:
            logger.debug(f"Postgres get_overage error: {e}")

    # Memory
    if user_id in _mem_overage:
        return _mem_overage[user_id]

    # default
    today = date.today()
    return 0, today


def set_overage(user_id: int, extra_seconds: int, last_reset_date: date):
    """
    Устанавливает докупленные секунды и дату их действия.
    Ожидается, что last_reset_date = сегодняшняя дата (если начисляем на сегодня).
    """
    # Redis
    if _redis:
        try:
            key = f"overage:{user_id}"
            _redis.hset(
                key,
                mapping={
                    "extra_seconds": int(extra_seconds),
                    "last_reset_date": last_reset_date.isoformat(),
                },
            )
            _redis.expire(key, 60 * 60 * 24 * 3)
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_overage (user_id, extra_seconds, last_reset_date)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        extra_seconds = EXCLUDED.extra_seconds,
                        last_reset_date = EXCLUDED.last_reset_date
                    """,
                    (user_id, int(extra_seconds), last_reset_date),
                )
        except Exception as e:
            logger.debug(f"Postgres set_overage error: {e}")

    # Memory
    _mem_overage[user_id] = (int(extra_seconds), last_reset_date)


def add_overage_seconds(user_id: int, add_seconds: int):
    """
    Начисляет докупленные секунды на СЕГОДНЯ. Если ранее было на прошлую дату — обнуляем и начисляем на сегодня.
    """
    cur_extra, last = get_overage(user_id)
    today = date.today()
    if last != today:
        cur_extra = 0
        last = today
    set_overage(user_id, cur_extra + max(0, int(add_seconds)), last)


def consume_overage_seconds(user_id: int, consume_seconds: int):
    """
    Списывает докупленные секунды (на сегодня). Если дата отлична от сегодняшней — ничего не списываем.
    """
    cur_extra, last = get_overage(user_id)
    today = date.today()
    if last != today:
        # Вчерашние докупки не действуют — ничего не списываем.
        return
    remain = max(0, cur_extra - max(0, int(consume_seconds)))
    set_overage(user_id, remain, today)
