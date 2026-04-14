import fnmatch
import json
import logging

import redis
from sqlalchemy.orm import Session

from app.models.rule import Rule

logger = logging.getLogger(__name__)

_CACHE_KEY = "rg:rules:all"
_CACHE_TTL = 60  # seconds


class RuleRepository:
    """
    Persists rules in SQLite; caches the full list in Redis.

    S — responsible only for rule storage and retrieval.
    L — fully substitutable; all methods raise or return as documented.
    """

    # ── Write ────────────────────────────────────────────────────────────────

    @staticmethod
    def create(db: Session, rule: Rule) -> Rule:
        db.add(rule)
        db.commit()
        db.refresh(rule)
        RuleRepository._invalidate_cache()
        return rule

    @staticmethod
    def set_active(db: Session, rule: Rule, active: bool) -> Rule:
        rule.active = active
        db.commit()
        RuleRepository._invalidate_cache()
        return rule

    @staticmethod
    def delete(db: Session, rule: Rule) -> None:
        db.delete(rule)
        db.commit()
        RuleRepository._invalidate_cache()

    # ── Read ─────────────────────────────────────────────────────────────────

    @staticmethod
    def get_all(db: Session) -> list[Rule]:
        return db.query(Rule).order_by(Rule.created_at.desc()).all()

    @staticmethod
    def get_by_id(db: Session, rule_id: int) -> Rule | None:
        return db.query(Rule).filter(Rule.id == rule_id).first()

    @staticmethod
    def match(db: Session, path: str, redis_client: redis.Redis) -> Rule | None:
        """
        Return the first active rule whose path_pattern matches *path*.
        Checks Redis cache first; falls back to SQLite on miss.
        """
        rules = RuleRepository._load_from_cache(redis_client)
        if rules is None:
            rules = RuleRepository._load_into_cache(db, redis_client)

        for rule in rules:
            if rule["active"] and fnmatch.fnmatch(path, rule["path_pattern"]):
                return Rule(**{k: v for k, v in rule.items()})
        return None

    # ── Cache helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _load_from_cache(redis_client: redis.Redis) -> list[dict] | None:
        raw = redis_client.get(_CACHE_KEY)
        return json.loads(raw) if raw else None

    @staticmethod
    def _load_into_cache(db: Session, redis_client: redis.Redis) -> list[dict]:
        rules = db.query(Rule).filter(Rule.active == True).order_by(Rule.created_at).all()
        payload = [
            {
                "id": r.id, "name": r.name, "path_pattern": r.path_pattern,
                "limit": r.limit, "window_seconds": r.window_seconds,
                "key_type": r.key_type, "active": r.active,
                "created_at": r.created_at.isoformat(),
            }
            for r in rules
        ]
        redis_client.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(payload))
        return payload

    @staticmethod
    def _invalidate_cache() -> None:
        from app.core.redis_client import get_redis
        try:
            get_redis().delete(_CACHE_KEY)
        except Exception as exc:
            logger.warning("Cache invalidation failed: %s", exc)
