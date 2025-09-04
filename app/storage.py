# app/storage.py
import os
import logging
from typing import Optional, Tuple, Dict, Set
import secrets
import string
from datetime import date, datetime, timedelta

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
            # PRO пользователи (постоянный)
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
            # Таблица обработанных платежей (идемпотентность)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_payments (
              provider TEXT NOT NULL,
              payment_id TEXT NOT NULL,
              processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (provider, payment_id)
            );
            """)

            # === РЕФЕРАЛКИ ===
            # реф.код пользователя
            cur.execute("""
            CREATE TABLE IF NOT EXISTS referral_codes (
              user_id BIGINT PRIMARY KEY,
              code TEXT UNIQUE NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """)
            # привязка "referred -> referrer"
            cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
              referred_id BIGINT PRIMARY KEY,
              referrer_id BIGINT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              first_rewarded BOOLEAN NOT NULL DEFAULT FALSE,
              first_rewarded_at DATE
            );
            """)

            # временный PRO до даты (включительно)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS pro_until (
              user_id BIGINT PRIMARY KEY,
              until_date DATE NOT NULL
            );
            """)

            # выданные «пороги» (чтобы не выдавать повторно)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS referral_tier_rewards (
              user_id BIGINT NOT NULL,
              tier INTEGER NOT NULL,
              awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY (user_id, tier)
            );
            """)
        logger.info("✅ Postgres подключен, таблицы готовы")
    except Exception as e:
        logger.warning(f"⚠️ Postgres недоступен: {e}")
        _pg_conn = None

# ---- Memory fallback ----
# user_usage: сколько базовых секунд из дневного лимита уже израсходовано сегодня
_mem_usage: dict[int, Tuple[int, date]] = {}  # user_id -> (used_seconds:int, last_reset_date:date)
# pro_users: множество PRO пользователей (постоянный)
_mem_pro: set[int] = set()
# user_overage: докупленные секунды на сегодня
_mem_overage: dict[int, Tuple[int, date]] = {}  # user_id -> (extra_seconds:int, last_reset_date:date)
# processed_payments: идемпотентность платежей
_mem_processed: set[tuple[str, str]] = set()

# временный PRO
_mem_pro_until: dict[int, date] = {}  # user_id -> until_date

# рефералки: коды и привязки
_mem_ref_code_by_user: dict[int, str] = {}
_mem_user_by_ref_code: dict[str, int] = {}
# referred_id -> (referrer_id, first_rewarded: bool, first_rewarded_at: Optional[date])
_mem_referrals: dict[int, Tuple[int, bool, Optional[date]]] = {}
# выданные пороги
_mem_ref_tier_awarded: dict[int, Set[int]] = {}

# ============================================================
#                 БАЗОВЫЙ ЛИМИТ (user_usage)
# ============================================================

def get_usage(user_id: int) -> Tuple[int, date]:
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

    if user_id in _mem_usage:
        return _mem_usage[user_id]

    today = date.today()
    return 0, today


def set_usage(user_id: int, used_seconds: int, last_reset_date: date):
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
            _redis.expire(key, 60 * 60 * 24 * 3)
        except Exception:
            pass

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

    _mem_usage[user_id] = (int(used_seconds), last_reset_date)

# ============================================================
#                        PRO СТАТУС
# ============================================================

def get_pro_until(user_id: int) -> Optional[date]:
    if _redis:
        try:
            v = _redis.get(f"pro:until:{user_id}")
            if v:
                return date.fromisoformat(v)
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT until_date FROM pro_until WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            logger.debug(f"Postgres get_pro_until error: {e}")
    return _mem_pro_until.get(user_id)

def add_pro_for_days(user_id: int, days: int) -> None:
    days = max(0, int(days))
    if days == 0:
        return
    today = date.today()
    cur_until = get_pro_until(user_id) or today
    start = max(today, cur_until)
    new_until = start + timedelta(days=days)

    if _redis:
        try:
            _redis.set(f"pro:until:{user_id}", new_until.isoformat(), ex=60*60*24*120)
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO pro_until (user_id, until_date)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET until_date = GREATEST(pro_until.until_date, EXCLUDED.until_date)
                """, (user_id, new_until))
        except Exception as e:
            logger.debug(f"Postgres add_pro_for_days error: {e}")
    _mem_pro_until[user_id] = new_until

def get_pro_remaining_days(user_id: int) -> int:
    u = get_pro_until(user_id)
    if not u:
        return 0
    return max(0, (u - date.today()).days + 1)

def is_pro(user_id: int) -> bool:
    # постоянный PRO
    perm = False
    if _redis:
        try:
            perm = bool(_redis.sismember("pro_users", user_id))
        except Exception:
            pass
    if not perm and _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pro_users WHERE user_id=%s", (user_id,))
                perm = cur.fetchone() is not None
        except Exception as e:
            logger.debug(f"Postgres is_pro permanent error: {e}")
    if not perm:
        perm = user_id in _mem_pro
    if perm:
        return True

    # временный PRO
    u = get_pro_until(user_id)
    return bool(u and u >= date.today())


def add_pro(user_id: int):
    if _redis:
        try:
            _redis.sadd("pro_users", user_id)
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pro_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (user_id,),
                )
        except Exception as e:
            logger.debug(f"Postgres add_pro error: {e}")
    _mem_pro.add(user_id)


def remove_pro(user_id: int):
    if _redis:
        try:
            _redis.srem("pro_users", user_id)
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("DELETE FROM pro_users WHERE user_id=%s", (user_id,))
        except Exception as e:
            logger.debug(f"Postgres remove_pro error: {e}")
    _mem_pro.discard(user_id)


def count_pro() -> int:
    if _redis:
        try:
            return int(_redis.scard("pro_users"))
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT COUNT(1) FROM pro_users")
                (cnt,) = cur.fetchone()
                return int(cnt)
        except Exception as e:
            logger.debug(f"Postgres count_pro error: {e}")
    return len(_mem_pro)

# ============================================================
#                   ДОКУПКА СЕКУНД (user_overage)
# ============================================================

def get_overage(user_id: int) -> Tuple[int, date]:
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

    if user_id in _mem_overage:
        return _mem_overage[user_id]

    today = date.today()
    return 0, today


def set_overage(user_id: int, extra_seconds: int, last_reset_date: date):
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

    _mem_overage[user_id] = (int(extra_seconds), last_reset_date)


def add_overage_seconds(user_id: int, add_seconds: int):
    cur_extra, last = get_overage(user_id)
    today = date.today()
    if last != today:
        cur_extra = 0
        last = today
    set_overage(user_id, cur_extra + max(0, int(add_seconds)), last)


def consume_overage_seconds(user_id: int, consume_seconds: int):
    cur_extra, last = get_overage(user_id)
    today = date.today()
    if last != today:
        return
    remain = max(0, cur_extra - max(0, int(consume_seconds)))
    set_overage(user_id, remain, today)

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

    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM processed_payments WHERE provider=%s AND payment_id=%s",
                    (provider, payment_id),
                )
                return cur.fetchone() is not None
        except Exception as e:
            logger.debug(f"Postgres is_payment_processed error: {e}")

    return (provider, payment_id) in _mem_processed


def mark_payment_processed(provider: str, payment_id: str):
    if not provider or not payment_id:
        return

    if _redis:
        try:
            key = f"pp:{provider}"
            _redis.sadd(key, payment_id)
            _redis.expire(key, 60 * 60 * 24 * 90)
        except Exception:
            pass

    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO processed_payments (provider, payment_id)
                    VALUES (%s, %s)
                    ON CONFLICT (provider, payment_id) DO NOTHING
                    """,
                    (provider, payment_id),
                )
        except Exception as e:
            logger.debug(f"Postgres mark_payment_processed error: {e}")

    _mem_processed.add((provider, payment_id))

# ============================================================
#                      РЕФЕРАЛЬНАЯ ПРОГРАММА
# ============================================================

# --- коды ---

def _mem_make_ref_code(uid: int) -> str:
    # простая, но стабильная генерация: uid в base36 + "r"
    import random, string
    if uid <= 0:
        uid = random.randint(1000, 10_000_000)
    base = format(uid, "x")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
    return f"{base}{suffix}"

def get_or_create_ref_code(user_id: int) -> str:
    # Redis
    if _redis:
        try:
            v = _redis.get(f"refcode:{user_id}")
            if v:
                return v
        except Exception:
            pass

    # Postgres
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT code FROM referral_codes WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
                if row:
                    code = str(row[0])
                else:
                    code = _mem_make_ref_code(user_id)
                    cur.execute(
                        "INSERT INTO referral_codes (user_id, code) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                        (user_id, code),
                    )
                # обновим Redis
                if _redis:
                    try:
                        _redis.set(f"refcode:{user_id}", code, ex=60*60*24*365)
                        _redis.set(f"refcode:rev:{code}", user_id, ex=60*60*24*365)
                    except Exception:
                        pass
                _mem_ref_code_by_user[user_id] = code
                _mem_user_by_ref_code[code] = user_id
                return code
        except Exception as e:
            logger.debug(f"Postgres get_or_create_ref_code error: {e}")

    # Memory
    if user_id in _mem_ref_code_by_user:
        return _mem_ref_code_by_user[user_id]
    code = _mem_make_ref_code(user_id)
    _mem_ref_code_by_user[user_id] = code
    _mem_user_by_ref_code[code] = user_id
    return code

def resolve_ref_code(code: str) -> Optional[int]:
    code = (code or "").strip()
    if not code:
        return None
    if _redis:
        try:
            v = _redis.get(f"refcode:rev:{code}")
            if v:
                try:
                    return int(v)
                except Exception:
                    return None
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT user_id FROM referral_codes WHERE code=%s", (code,))
                row = cur.fetchone()
                if row:
                    return int(row[0])
        except Exception as e:
            logger.debug(f"Postgres resolve_ref_code error: {e}")
    return _mem_user_by_ref_code.get(code)

# --- привязки ---

def bind_referral(referrer_id: int, referred_id: int) -> bool:
    """Возвращает True, если привязка создана (первая)."""
    if referrer_id == referred_id or referred_id <= 0 or referrer_id <= 0:
        return False

    # уже есть?
    if get_referrer(referred_id):
        return False

    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO referrals (referred_id, referrer_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (referred_id, referrer_id),
                )
                inserted = cur.rowcount > 0
        except Exception as e:
            logger.debug(f"Postgres bind_referral error: {e}")
            inserted = False
    else:
        inserted = referred_id not in _mem_referrals
        if inserted:
            _mem_referrals[referred_id] = (referrer_id, False, None)

    # кэш
    if inserted and _redis:
        try:
            _redis.hset(f"ref:{referred_id}", mapping={"referrer_id": referrer_id, "first_rewarded": 0})
        except Exception:
            pass
    return inserted

def get_referrer(referred_id: int) -> Optional[int]:
    if _redis:
        try:
            v = _redis.hget(f"ref:{referred_id}", "referrer_id")
            if v:
                return int(v)
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT referrer_id FROM referrals WHERE referred_id=%s", (referred_id,))
                row = cur.fetchone()
                if row:
                    return int(row[0])
        except Exception as e:
            logger.debug(f"Postgres get_referrer error: {e}")
    if referred_id in _mem_referrals:
        return _mem_referrals[referred_id][0]
    return None

def has_first_reward(referred_id: int) -> bool:
    if _redis:
        try:
            v = _redis.hget(f"ref:{referred_id}", "first_rewarded")
            if v is not None:
                return str(v) == "1"
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT first_rewarded FROM referrals WHERE referred_id=%s", (referred_id,))
                row = cur.fetchone()
                if row:
                    return bool(row[0])
        except Exception as e:
            logger.debug(f"Postgres has_first_reward error: {e}")
    if referred_id in _mem_referrals:
        return bool(_mem_referrals[referred_id][1])
    return False

def mark_referral_rewarded(referred_id: int) -> None:
    today = date.today()
    if _redis:
        try:
            _redis.hset(f"ref:{referred_id}", mapping={"first_rewarded": 1})
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE referrals SET first_rewarded=TRUE, first_rewarded_at=%s WHERE referred_id=%s",
                    (today, referred_id),
                )
        except Exception as e:
            logger.debug(f"Postgres mark_referral_rewarded error: {e}")
    if referred_id in _mem_referrals:
        ref_id, _, _ = _mem_referrals[referred_id]
        _mem_referrals[referred_id] = (ref_id, True, today)

def get_today_rewarded_count(referrer_id: int) -> int:
    today = date.today()
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(1) FROM referrals WHERE referrer_id=%s AND first_rewarded=TRUE AND first_rewarded_at=%s",
                    (referrer_id, today),
                )
                (cnt,) = cur.fetchone()
                return int(cnt)
        except Exception as e:
            logger.debug(f"Postgres get_today_rewarded_count error: {e}")
    # Memory/Redis best-effort
    cnt = 0
    for _, (ref_id, rewarded, dt) in _mem_referrals.items():
        if ref_id == referrer_id and rewarded and dt == today:
            cnt += 1
    return cnt

def get_ref_stats(user_id: int) -> Dict[str, int]:
    """total — сколько привязано к этому рефереру; rewarded — сколько уже получили «первую награду»."""
    total = 0
    rewarded = 0
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT COUNT(1) FROM referrals WHERE referrer_id=%s", (user_id,))
                (total,) = cur.fetchone()
                cur.execute("SELECT COUNT(1) FROM referrals WHERE referrer_id=%s AND first_rewarded=TRUE", (user_id,))
                (rewarded,) = cur.fetchone()
                return {"total": int(total), "rewarded": int(rewarded)}
        except Exception as e:
            logger.debug(f"Postgres get_ref_stats error: {e}")

    for _, (ref_id, rew, _) in _mem_referrals.items():
        if ref_id == user_id:
            total += 1
            if rew:
                rewarded += 1
    return {"total": total, "rewarded": rewarded}

# --- «Пороги» (трофеи) ---

def is_tier_awarded(user_id: int, tier: int) -> bool:
    if _redis:
        try:
            return bool(_redis.sismember(f"ref:tier:{user_id}", int(tier)))
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute("SELECT 1 FROM referral_tier_rewards WHERE user_id=%s AND tier=%s", (user_id, int(tier)))
                return cur.fetchone() is not None
        except Exception as e:
            logger.debug(f"Postgres is_tier_awarded error: {e}")
    return int(tier) in _mem_ref_tier_awarded.get(user_id, set())

def mark_tier_awarded(user_id: int, tier: int) -> None:
    if _redis:
        try:
            _redis.sadd(f"ref:tier:{user_id}", int(tier))
            _redis.expire(f"ref:tier:{user_id}", 60*60*24*365)
        except Exception:
            pass
    if _pg_conn:
        try:
            with _pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO referral_tier_rewards (user_id, tier) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (user_id, int(tier)),
                )
        except Exception as e:
            logger.debug(f"Postgres mark_tier_awarded error: {e}")
    s = _mem_ref_tier_awarded.setdefault(user_id, set())
    s.add(int(tier))
