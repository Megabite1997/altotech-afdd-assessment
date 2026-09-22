"""`afdd` command line. One entry point for setup, replay, evaluation and checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .config import settings

log = logging.getLogger("afdd.cli")


def _json(value) -> None:
    print(json.dumps(value, indent=2, default=str))


# ---------------------------------------------------------------------------
# database and seed
# ---------------------------------------------------------------------------

def cmd_migrate(_: argparse.Namespace) -> int:
    db.wait_for_db()
    _json({"applied": db.migrate()})
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    db.wait_for_db()
    db.execute(
        """
        TRUNCATE observation, point_current, ingest_event, ingest_reject,
                 issue, issue_observation, issue_event, evaluator_state, evaluation_run,
                 backtest_run, agent_request, agent_step RESTART IDENTITY CASCADE
        """
    )
    if args.all:
        db.execute("TRUNCATE rule, rule_version, rule_activation RESTART IDENTITY CASCADE")
        db.execute("TRUNCATE point_registry, ontology_relation, space_closure CASCADE")
        db.execute("TRUNCATE ontology_entity CASCADE")
    _json({"reset": "ok", "ontology_and_rules_cleared": bool(args.all)})
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from .ontology.loader import load
    from .rules import store as rule_store
    from .rules.defaults import definitions

    db.wait_for_db()
    db.migrate()
    report = load(Path(args.source_dir or settings.source_dir))
    activated = []
    for definition in definitions():
        version = rule_store.save_version(definition, notes="seeded default", created_by="seed")
        if definition.key == "ahu-supply-air-deviation":
            rule_store.activate(definition.key, version, actor="seed", source="seed")
            activated.append({"rule_key": definition.key, "version": version})
        else:
            activated.append({"rule_key": definition.key, "version": version, "status": "draft"})
    _json({"ontology": report.as_dict(), "rules": activated})
    return 0


# ---------------------------------------------------------------------------
# simulation and ingestion
# ---------------------------------------------------------------------------

def cmd_simulate(args: argparse.Namespace) -> int:
    from .simulator.replay import Simulator

    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    simulator = Simulator(
        source_dir=Path(args.source_dir) if args.source_dir else None,
        interval=args.interval,
        speedup=args.speedup,
        buildings=args.buildings.split(",") if args.buildings else None,
        loop=args.loop,
    )
    published = asyncio.run(simulator.run())
    _json({"published": published})
    return 0


def cmd_ingest(_: argparse.Namespace) -> int:
    from .ingest.consumer import main

    asyncio.run(main())
    return 0


def cmd_replay_direct(args: argparse.Namespace) -> int:
    """Load the source fixtures straight into the store, bypassing the broker.

    Same envelope, same writer, same idempotency - just no network. Used by tests
    and by anyone who wants the seeded outcomes without waiting for a replay.
    """
    from .ingest.writer import PointResolver, ingest_event
    from .simulator.replay import events

    db.wait_for_db()
    source = Path(args.source_dir or settings.source_dir)
    buildings = args.buildings.split(",") if args.buildings else None
    counters: dict[str, int] = {}
    total = 0
    with db.connection() as conn:
        with conn.cursor() as cur:
            resolver = PointResolver(cur)
            for _, event in events(source, buildings):
                payload = event.to_dict()
                result = ingest_event(cur, payload, resolver)
                counters[result.status] = counters.get(result.status, 0) + 1
                total += 1
                if total % 5000 == 0:
                    conn.commit()
                    print(f"  ... {total} events", file=sys.stderr)
        conn.commit()
    _json({"events": total, "by_status": counters})
    return 0


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluator import worker

    db.wait_for_db()
    if args.once:
        _json(worker.run_once())
        return 0
    worker.run_forever()
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .evaluator import backtest
    from .rules import store as rule_store

    db.wait_for_db()
    found = rule_store.get_definition(args.rule, args.version)
    if found is None:
        print(f"unknown rule {args.rule}", file=sys.stderr)
        return 1
    _, definition = found
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    _json(backtest.run(definition, start, end, args.label))
    return 0


# ---------------------------------------------------------------------------
# agent
# ---------------------------------------------------------------------------

def cmd_agent_run(args: argparse.Namespace) -> int:
    from .agent.orchestrator import RuleAuthoringAgent
    from .agent.providers import build_provider

    db.wait_for_db()
    provider = build_provider(args.provider) if args.provider else None
    _json(RuleAuthoringAgent(provider=provider).run(args.request))
    return 0


def cmd_agent_evaluate(args: argparse.Namespace) -> int:
    from .agent.cases import run_matrix

    db.wait_for_db()
    summary = run_matrix(args.provider, Path(args.output) if args.output else None)
    print(f"{'case':<22} {'category':<20} {'expected':<22} {'actual':<22} result")
    for row in summary["cases"]:
        mark = "pass" if row["passed"] else "FAIL"
        print(f"{row['case']:<22} {row['category']:<20} {row['expected']:<22} {row['stop_reason']:<22} {mark}")
        for problem in row["problems"]:
            print(f"    - {problem}")
    print(f"\n{summary['passed']}/{summary['total']} passed "
          f"(provider={summary['cases'][0]['provider'] if summary['cases'] else 'n/a'})")
    return 0 if summary["passed"] == summary["total"] else 1


# ---------------------------------------------------------------------------
# verification and demo aids
# ---------------------------------------------------------------------------

EXPECTED_INVENTORY = {"spaces": 93, "equipment": 84, "points": 288}


def cmd_verify(_: argparse.Namespace) -> int:
    from .ontology import queries as onto

    db.wait_for_db()
    totals = onto.counts()["totals"]
    checks = [
        ("spaces", totals["spaces"], EXPECTED_INVENTORY["spaces"]),
        ("equipment", totals["equipment"], EXPECTED_INVENTORY["equipment"]),
        ("points", totals["points"], EXPECTED_INVENTORY["points"]),
    ]
    ok = True
    print(f"{'check':<28} {'actual':>8} {'expected':>10}  result")
    for name, actual, expected in checks:
        good = actual == expected
        ok = ok and good
        print(f"{name:<28} {actual:>8} {expected:>10}  {'pass' if good else 'FAIL'}")

    issues = db.query(
        """
        SELECT equipment_id, state, opened_at, closed_at, close_reason, calculated_difference,
               threshold, threshold_source
          FROM issue WHERE backtest_run_id IS NULL ORDER BY opened_at
        """
    )
    print("\nissues detected from the supplied fixture:")
    for issue in issues:
        print(f"  {issue['equipment_id']:<20} opened {issue['opened_at']} "
              f"diff={issue['calculated_difference']:.2f} threshold={issue['threshold']} "
              f"({issue['threshold_source']}) -> {issue['state']} {issue['close_reason'] or ''}")

    quality = db.query(
        "SELECT reason_code, count(*) AS n FROM ingest_reject GROUP BY reason_code ORDER BY n DESC"
    )
    print("\ndata-quality outcomes:")
    for row in quality:
        print(f"  {row['reason_code']:<24} {row['n']}")
    return 0 if ok else 1


def cmd_remove_point(args: argparse.Namespace) -> int:
    """Demo aid: drop a point from the ontology to show visible exclusion."""
    db.wait_for_db()
    db.execute("DELETE FROM point_registry WHERE point_id = %s", (args.point_id,))
    db.execute("DELETE FROM ontology_entity WHERE entity_id = %s", (args.point_id,))
    _json({"removed": args.point_id})
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="afdd", description="AltoTech AFDD platform CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    database = sub.add_parser("db", help="database maintenance").add_subparsers(dest="sub", required=True)
    database.add_parser("migrate", help="apply migrations").set_defaults(func=cmd_migrate)
    reset = database.add_parser("reset", help="clear telemetry, issues and agent state")
    reset.add_argument("--all", action="store_true", help="also clear ontology and rules")
    reset.set_defaults(func=cmd_reset)

    seed = sub.add_parser("seed", help="migrate, load the ontology and install the default rules")
    seed.add_argument("--source-dir")
    seed.set_defaults(func=cmd_seed)

    sim = sub.add_parser("simulate", help="stream source readings to the broker")
    sim.add_argument("--interval", type=int, default=None, help="publish cadence in seconds (15 or 60)")
    sim.add_argument("--speedup", type=float, default=None, help="source seconds per wall second")
    sim.add_argument("--buildings", help="comma separated, e.g. building-a,building-b")
    sim.add_argument("--source-dir")
    sim.add_argument("--loop", action="store_true")
    sim.set_defaults(func=cmd_simulate)

    sub.add_parser("ingest", help="run the broker consumer").set_defaults(func=cmd_ingest)

    direct = sub.add_parser("replay", help="load fixtures straight into the store (no broker)")
    direct.add_argument("--source-dir")
    direct.add_argument("--buildings")
    direct.set_defaults(func=cmd_replay_direct)

    evaluate = sub.add_parser("evaluate", help="run the AFDD evaluator")
    evaluate.add_argument("--once", action="store_true")
    evaluate.set_defaults(func=cmd_evaluate)

    back = sub.add_parser("backtest", help="replay a rule over a historical window")
    back.add_argument("--rule", required=True)
    back.add_argument("--version", type=int)
    back.add_argument("--start", required=True, help="ISO timestamp")
    back.add_argument("--end", required=True, help="ISO timestamp")
    back.add_argument("--label", default="")
    back.set_defaults(func=cmd_backtest)

    agent = sub.add_parser("agent", help="AI rule authoring").add_subparsers(dest="sub", required=True)
    run = agent.add_parser("run", help="draft a rule from a natural-language request")
    run.add_argument("request")
    run.add_argument("--provider", choices=["anthropic", "stub"])
    run.set_defaults(func=cmd_agent_run)
    matrix = agent.add_parser("evaluate", help="run the repeatable case matrix")
    matrix.add_argument("--provider", choices=["anthropic", "stub"])
    matrix.add_argument("--output")
    matrix.set_defaults(func=cmd_agent_evaluate)

    sub.add_parser("verify", help="check inventory counts and seeded outcomes").set_defaults(func=cmd_verify)

    demo = sub.add_parser("remove-point", help="demo aid: remove a point from the ontology")
    demo.add_argument("point_id")
    demo.set_defaults(func=cmd_remove_point)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
