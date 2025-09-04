# app/storage.py
import logging
from typing import Optional, Tuple
from datetime import date

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
        logger.warning("⚠️ Redis недоступен: %s", e)
        _redis = None

# ---- Postgres (опционально) ----
_pg_conn = None

def _pg_connect():
    global _pg_conn
    if not DATABASE_URL:
        return None
    try:
        import psycopg  # type: ignore
        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg.connect(DATABASE_URL, autocommit=True)
        return _pg_conn
    except Exception as e:
        logger.warning("⚠️ Postgres недоступен: %s", e)
        return None

def _pg_cursor():
    conn = _pg_connect()
    if conn is None:
        return None
    try:
        return conn.cursor()
    except Exception as e:
        logger.warning("⚠️ Не удалось открыть курсор PG: %s", e)
        return None

def _pg_init_schema():
    cur = _pg_cursor()
    if cur is None:
        return
    try:
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_overage (
          user_id BIGINT PRIMARY KEY,
          extra_seconds INTEGER NOT NULL DEFAULT 0,
          last_reset_date DATE NOT NULL
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_payments (
          provider TEXT NOT NULL,
          payment_id TEXT NOT NULL,
          processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (provider, payment_id)
        );
        """)
        logger.info("✅ Postgres подключен, таблицы готовы")
    except Exception as e:
        logger.warning("⚠️ Ошибка инициализации схемы PG: %s", e)
    finally:
        try:
            cur.close()
        except Exception:
            pass

if DATABASE_URL:
    _pg_init_schema()

# ---- Memory fallback ----
_mem_usage: dict[int, Tuple[int, date]] = {}
_mem_pro: set[int] = set()
_mem_overage: dict[int, Tuple[int, date]] = {}
_mem_processed: set[tuple[str, str]] = set()

# ============================================================
#                 БАЗОВЫЙ ЛИМИТ (user_usage)
# ============================================================

def get_usage(user_id: int) -> Tuple[int, date]:
    """Возвращает (used_seconds, last_reset_date) по базовому лимиту."""
    # Redis
    if _redis:
        try:
            key = f"usage:{user_id}"
            used_s = _redis.hget(key, "used_seconds")
            last = _redis.hget(key, "last_reset_date")
            if used_s is not None and last is not None:
                return int(used_s), date.fromisoformat(last)
        except Exception:
            pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("SELECT used_seconds, last_reset_date FROM user_usage WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row:
                return int(row[0]), row[1]
        except Exception as e:
            logger.debug("Postgres get_usage error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    # Memory
    if user_id in _mem_usage:
        return _mem_usage[user_id]

    return 0, date.today()

def set_usage(user_id: int, used_seconds: int, last_reset_date: date):
    """Устанавливает (used_seconds, last_reset_date) по базовому лимиту."""
    # Redis
    if _redis:
        try:
            key = f"usage:{user_id}"
            _redis.hset(key, mapping={
                "used_seconds": int(used_seconds),
                "last_reset_date": last_reset_date.isoformat(),
            })
            _redis.expire(key, 60 * 60 * 24 * 3)
        except Exception:
            pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("""
                INSERT INTO user_usage (user_id, used_seconds, last_reset_date)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    used_seconds = EXCLUDED.used_seconds,
                    last_reset_date = EXCLUDED.last_reset_date
            """, (user_id, int(used_seconds), last_reset_date))
        except Exception as e:
            logger.debug("Postgres set_usage error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    _mem_usage[user_id] = (int(used_seconds), last_reset_date)

# ============================================================
#                        PRO СТАТУС
# ============================================================

def is_pro(user_id: int) -> bool:
    # Redis
    if _redis:
        try:
            return bool(_redis.sismember("pro_users", user_id))
        except Exception:
            pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("SELECT 1 FROM pro_users WHERE user_id=%s", (user_id,))
            return cur.fetchone() is not None
        except Exception as e:
            logger.debug("Postgres is_pro error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    return user_id in _mem_pro

def add_pro(user_id: int):
    # Redis
    if _redis:
        try: _redis.sadd("pro_users", user_id)
        except Exception: pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("INSERT INTO pro_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
        except Exception as e:
            logger.debug("Postgres add_pro error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    _mem_pro.add(user_id)

def remove_pro(user_id: int):
    # Redis
    if _redis:
        try: _redis.srem("pro_users", user_id)
        except Exception: pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("DELETE FROM pro_users WHERE user_id=%s", (user_id,))
        except Exception as e:
            logger.debug("Postgres remove_pro error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    _mem_pro.discard(user_id)

def count_pro() -> int:
    # Redis (оценочно)
    if _redis:
        try:
            return int(_redis.scard("pro_users"))
        except Exception:
            pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("SELECT COUNT(1) FROM pro_users")
            (cnt,) = cur.fetchone()
            return int(cnt or 0)
        except Exception as e:
            logger.debug("Postgres count_pro error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    return len(_mem_pro)

# ============================================================
#                   ДОКУПКА СЕКУНД (user_overage)
# ============================================================

def get_overage(user_id: int) -> Tuple[int, date]:
    # Redis
    if _redis:
        try:
            key = f"overage:{user_id}"
            extra = _redis.hget(key, "extra_seconds")
            last = _redis.hget(key, "last_reset_date")
            if extra is not None and last is not None:
                return int(extra), date.fromisoformat(last)
        except Exception:
            pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("SELECT extra_seconds, last_reset_date FROM user_overage WHERE user_id=%s", (user_id,))
            row = cur.fetchone()
            if row:
                return int(row[0]), row[1]
        except Exception as e:
            logger.debug("Postgres get_overage error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    if user_id in _mem_overage:
        return _mem_overage[user_id]

    return 0, date.today()

def set_overage(user_id: int, extra_seconds: int, last_reset_date: date):
    # Redis
    if _redis:
        try:
            key = f"overage:{user_id}"
            _redis.hset(key, mapping={
                "extra_seconds": int(extra_seconds),
                "last_reset_date": last_reset_date.isoformat(),
            })
            _redis.expire(key, 60 * 60 * 24 * 3)
        except Exception:
            pass

    # Postgres
    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("""
                INSERT INTO user_overage (user_id, extra_seconds, last_reset_date)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    extra_seconds = EXCLUDED.extra_seconds,
                    last_reset_date = EXCLUDED.last_reset_date
            """, (user_id, int(extra_seconds), last_reset_date))
        except Exception as e:
            logger.debug("Postgres set_overage error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    _mem_overage[user_id] = (int(extra_seconds), last_reset_date)

def add_overage_seconds(user_id: int, add_seconds: int):
    cur_extra, last = get_overage(user_id)
    today = date.today()
    if last != today:
        cur_extra, last = 0, today
    set_overage(user_id, cur_extra + max(0, int(add_seconds)), last)

def consume_overage_seconds(user_id: int, consume_seconds: int):
    cur_extra, last = get_overage(user_id)
    if last != date.today():
        return
    remain = max(0, cur_extra - max(0, int(consume_seconds)))
    set_overage(user_id, remain, date.today())

# ============================================================
#                ИДЕМПОТЕНТНОСТЬ ПЛАТЕЖЕЙ
# ============================================================

def is_payment_processed(provider: str, payment_id: str) -> bool:
    if not provider or not payment_id:
        return False

    if _redis:
        try:
            return bool(_redis.sismember(f"pp:{provider}", payment_id))
        except Exception:
            pass

    cur = _pg_cursor()
    if cur:
        try:
            cur.execute(
                "SELECT 1 FROM processed_payments WHERE provider=%s AND payment_id=%s",
                (provider, payment_id),
            )
            return cur.fetchone() is not None
        except Exception as e:
            logger.debug("Postgres is_payment_processed error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    return (provider, payment_id) in _mem_processed

def mark_payment_processed(provider: str, payment_id: str):
    if not provider or not payment_id:
        return

    if _redis:
        try:
            key = f"pp:{provider}"
            _redis.sadd(key, payment_id)
            _redis.expire(key, 60 * 60 * 24 * 90)  # 90 дней
        except Exception:
            pass

    cur = _pg_cursor()
    if cur:
        try:
            cur.execute("""
                INSERT INTO processed_payments (provider, payment_id)
                VALUES (%s, %s)
                ON CONFLICT (provider, payment_id) DO NOTHING
            """, (provider, payment_id))
        except Exception as e:
            logger.debug("Postgres mark_payment_processed error: %s", e)
        finally:
            try: cur.close()
            except Exception: pass

    _mem_processed.add((provider, payment_id))
