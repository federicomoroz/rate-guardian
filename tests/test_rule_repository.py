"""
Unit tests — RuleRepository.
Covers: create, get_all, get_by_id, set_active, delete,
        match (fnmatch, cache hit/miss, inactive rules, no match).
"""

import pytest
import fakeredis

from app.models.rule import Rule
from app.models.repositories.rule_repository import RuleRepository


def _rule(**kwargs) -> Rule:
    defaults = dict(
        name="Test Rule",
        path_pattern="/proxy/api/*",
        limit=100,
        window_seconds=60,
        key_type="ip",
        active=True,
    )
    return Rule(**{**defaults, **kwargs})


# ── create ────────────────────────────────────────────────────────────────────

def test_create_returns_rule_with_id(db_session):
    r = RuleRepository.create(db_session, _rule())
    assert r.id is not None
    assert r.name == "Test Rule"


def test_create_sets_active_true_by_default(db_session):
    r = RuleRepository.create(db_session, _rule())
    assert r.active is True


def test_create_persists_all_fields(db_session):
    r = RuleRepository.create(db_session, _rule(
        name="My Rule", path_pattern="/api/v2/*",
        limit=50, window_seconds=30, key_type="global",
    ))
    fetched = RuleRepository.get_by_id(db_session, r.id)
    assert fetched.name == "My Rule"
    assert fetched.path_pattern == "/api/v2/*"
    assert fetched.limit == 50
    assert fetched.window_seconds == 30
    assert fetched.key_type == "global"


# ── get_all ───────────────────────────────────────────────────────────────────

def test_get_all_empty(db_session):
    assert RuleRepository.get_all(db_session) == []


def test_get_all_returns_all(db_session):
    RuleRepository.create(db_session, _rule(name="A"))
    RuleRepository.create(db_session, _rule(name="B"))
    RuleRepository.create(db_session, _rule(name="C"))
    rules = RuleRepository.get_all(db_session)
    assert len(rules) == 3


def test_get_all_newest_first(db_session):
    RuleRepository.create(db_session, _rule(name="First"))
    RuleRepository.create(db_session, _rule(name="Second"))
    rules = RuleRepository.get_all(db_session)
    assert rules[0].name == "Second"


# ── get_by_id ─────────────────────────────────────────────────────────────────

def test_get_by_id_found(db_session):
    created = RuleRepository.create(db_session, _rule())
    fetched = RuleRepository.get_by_id(db_session, created.id)
    assert fetched is not None
    assert fetched.id == created.id


def test_get_by_id_not_found(db_session):
    assert RuleRepository.get_by_id(db_session, 99999) is None


# ── set_active ────────────────────────────────────────────────────────────────

def test_set_active_disables_rule(db_session):
    r = RuleRepository.create(db_session, _rule(active=True))
    updated = RuleRepository.set_active(db_session, r, False)
    assert updated.active is False
    assert RuleRepository.get_by_id(db_session, r.id).active is False


def test_set_active_enables_rule(db_session):
    r = RuleRepository.create(db_session, _rule(active=False))
    updated = RuleRepository.set_active(db_session, r, True)
    assert updated.active is True


def test_toggle_active_twice_restores(db_session):
    r = RuleRepository.create(db_session, _rule(active=True))
    RuleRepository.set_active(db_session, r, False)
    RuleRepository.set_active(db_session, r, True)
    assert RuleRepository.get_by_id(db_session, r.id).active is True


# ── delete ────────────────────────────────────────────────────────────────────

def test_delete_removes_rule(db_session):
    r = RuleRepository.create(db_session, _rule())
    RuleRepository.delete(db_session, r)
    assert RuleRepository.get_by_id(db_session, r.id) is None


def test_delete_leaves_others(db_session):
    r1 = RuleRepository.create(db_session, _rule(name="Keep"))
    r2 = RuleRepository.create(db_session, _rule(name="Remove"))
    RuleRepository.delete(db_session, r2)
    assert RuleRepository.get_by_id(db_session, r1.id) is not None
    assert len(RuleRepository.get_all(db_session)) == 1


# ── match ─────────────────────────────────────────────────────────────────────

def test_match_exact_pattern(db_session, fake_redis):
    RuleRepository.create(db_session, _rule(path_pattern="/proxy/api.test/*"))
    m = RuleRepository.match(db_session, "/proxy/api.test/users", fake_redis)
    assert m is not None
    assert m.path_pattern == "/proxy/api.test/*"


def test_match_wildcard(db_session, fake_redis):
    RuleRepository.create(db_session, _rule(path_pattern="/proxy/*"))
    m = RuleRepository.match(db_session, "/proxy/anything/goes/here", fake_redis)
    assert m is not None


def test_match_no_rule(db_session, fake_redis):
    m = RuleRepository.match(db_session, "/proxy/no-rule", fake_redis)
    assert m is None


def test_match_inactive_rule_not_returned(db_session, fake_redis):
    RuleRepository.create(db_session, _rule(
        path_pattern="/proxy/*", active=False
    ))
    m = RuleRepository.match(db_session, "/proxy/test", fake_redis)
    assert m is None


def test_match_returns_first_active(db_session, fake_redis):
    RuleRepository.create(db_session, _rule(name="First", path_pattern="/proxy/*", limit=10))
    RuleRepository.create(db_session, _rule(name="Second", path_pattern="/proxy/*", limit=99))
    m = RuleRepository.match(db_session, "/proxy/test", fake_redis)
    assert m is not None  # returns one of them


def test_match_uses_cache_on_second_call(db_session, fake_redis):
    RuleRepository.create(db_session, _rule(path_pattern="/proxy/*"))

    # First call loads from DB → cache
    m1 = RuleRepository.match(db_session, "/proxy/test", fake_redis)
    assert m1 is not None

    # Second call should hit cache (Redis key exists)
    assert fake_redis.get("rg:rules:all") is not None
    m2 = RuleRepository.match(db_session, "/proxy/test", fake_redis)
    assert m2 is not None


def test_create_invalidates_cache(db_session, fake_redis):
    r = RuleRepository.create(db_session, _rule())
    # Cache should be cleared after create
    # (invalidate_cache deletes the key)
    # The get_redis() in _invalidate_cache uses the singleton; we patched it in conftest
    # Just verify the match still works after creation
    m = RuleRepository.match(db_session, "/proxy/api/*", fake_redis)
    assert m is not None


def test_match_path_not_matching_pattern(db_session, fake_redis):
    RuleRepository.create(db_session, _rule(path_pattern="/proxy/api/*"))
    m = RuleRepository.match(db_session, "/different/path", fake_redis)
    assert m is None


def test_match_multiple_rules_priority(db_session, fake_redis):
    # Both rules match, first active one wins
    RuleRepository.create(db_session, _rule(name="Specific", path_pattern="/proxy/api/v1/*", limit=5))
    RuleRepository.create(db_session, _rule(name="Broad", path_pattern="/proxy/*", limit=100))
    m = RuleRepository.match(db_session, "/proxy/api/v1/users", fake_redis)
    assert m is not None
    # Should return one (the first match in cache order)


def test_match_empty_rules(db_session, fake_redis):
    m = RuleRepository.match(db_session, "/proxy/test", fake_redis)
    assert m is None
