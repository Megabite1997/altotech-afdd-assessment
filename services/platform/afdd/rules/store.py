"""Rule persistence: immutable versions, explicit activation."""

from __future__ import annotations

from psycopg.types.json import Jsonb

from .. import db
from .schema import RuleDefinition, validate_definition


def list_rules() -> list[dict]:
    return db.query(
        """
        SELECT r.rule_key, r.name, r.intent, r.status, r.active_version, r.updated_at,
               (SELECT max(version) FROM rule_version v WHERE v.rule_key = r.rule_key) AS latest_version,
               (SELECT count(*) FROM issue i
                 WHERE i.rule_key = r.rule_key AND i.state = 'open' AND i.backtest_run_id IS NULL) AS open_issues
          FROM rule r
         ORDER BY r.rule_key
        """
    )


def get_rule(rule_key: str) -> dict | None:
    rule = db.query_one("SELECT * FROM rule WHERE rule_key = %s", (rule_key,))
    if rule is None:
        return None
    rule["versions"] = db.query(
        """
        SELECT version, notes, created_by, created_at
          FROM rule_version WHERE rule_key = %s ORDER BY version DESC
        """,
        (rule_key,),
    )
    active = rule.get("active_version")
    rule["definition"] = None
    if active is not None:
        row = db.query_one(
            "SELECT definition FROM rule_version WHERE rule_key = %s AND version = %s",
            (rule_key, active),
        )
        rule["definition"] = row["definition"] if row else None
    return rule


def get_definition(rule_key: str, version: int | None = None) -> tuple[int, RuleDefinition] | None:
    if version is None:
        row = db.query_one(
            """
            SELECT v.version, v.definition
              FROM rule r JOIN rule_version v
                ON v.rule_key = r.rule_key AND v.version = COALESCE(r.active_version,
                     (SELECT max(version) FROM rule_version x WHERE x.rule_key = r.rule_key))
             WHERE r.rule_key = %s
            """,
            (rule_key,),
        )
    else:
        row = db.query_one(
            "SELECT version, definition FROM rule_version WHERE rule_key = %s AND version = %s",
            (rule_key, version),
        )
    if row is None:
        return None
    return row["version"], validate_definition(row["definition"])


def save_version(definition: RuleDefinition, *, notes: str = "", created_by: str = "system") -> int:
    """Append a new immutable version. Never mutates an existing one."""
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rule (rule_key, name, intent, status)
            VALUES (%s, %s, %s, 'draft')
            ON CONFLICT (rule_key) DO UPDATE
               SET name = EXCLUDED.name, intent = EXCLUDED.intent, updated_at = now()
            """,
            (definition.key, definition.name, definition.intent),
        )
        cur.execute(
            "SELECT COALESCE(max(version), 0) + 1 AS next FROM rule_version WHERE rule_key = %s",
            (definition.key,),
        )
        version = cur.fetchone()["next"]
        cur.execute(
            """
            INSERT INTO rule_version (rule_key, version, definition, notes, created_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (definition.key, version, Jsonb(definition.model_dump(mode="json")), notes, created_by),
        )
    return version


def activate(rule_key: str, version: int, actor: str = "operator", source: str = "api") -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM rule_version WHERE rule_key = %s AND version = %s", (rule_key, version)
        )
        if cur.fetchone() is None:
            raise ValueError(f"rule {rule_key} has no version {version}")
        cur.execute(
            "UPDATE rule SET status = 'active', active_version = %s, updated_at = now() WHERE rule_key = %s",
            (version, rule_key),
        )
        cur.execute(
            "INSERT INTO rule_activation (rule_key, version, action, actor, source) VALUES (%s, %s, 'activated', %s, %s)",
            (rule_key, version, actor, source),
        )
    return {"rule_key": rule_key, "version": version, "status": "active"}


def disable(rule_key: str, actor: str = "operator") -> dict:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE rule SET status = 'disabled', updated_at = now() WHERE rule_key = %s", (rule_key,)
        )
        cur.execute(
            """
            SELECT COALESCE(active_version, 0) AS v FROM rule WHERE rule_key = %s
            """,
            (rule_key,),
        )
        row = cur.fetchone()
        cur.execute(
            "INSERT INTO rule_activation (rule_key, version, action, actor) VALUES (%s, %s, 'disabled', %s)",
            (rule_key, row["v"] if row else 0, actor),
        )
    return {"rule_key": rule_key, "status": "disabled"}


def activation_history(rule_key: str) -> list[dict]:
    return db.query(
        "SELECT version, action, actor, source, at FROM rule_activation WHERE rule_key = %s ORDER BY at DESC",
        (rule_key,),
    )
