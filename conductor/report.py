"""Retro analysis over the ledger.

    python -m conductor.report --days 7

Prints spend by model and rule, then two heuristic lists:
- downgrade candidates: frontier calls that looked like cheap-model work
  (small input, small output, no explicit tag forcing them up)
- escalation candidates: cheap-model calls that were retried quickly with a
  near-identical request, suggesting the first answer wasn't good enough
"""

import argparse
import os
import time
from pathlib import Path

from .ledger import Ledger

FRONTIER = ("claude-fable-5", "claude-opus-4-8")
CHEAP = ("claude-haiku-4-5",)

ROOT = Path(os.environ.get("CONDUCTOR_HOME", Path(__file__).resolve().parents[1]))


def fmt_cost(c):
    return f"${c:,.4f}" if c is not None else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--db", default=str(ROOT / "conductor.db"))
    args = ap.parse_args()

    ledger = Ledger(args.db)
    since = time.time() - args.days * 86400

    print(f"== Conductor report: last {args.days} day(s) ==\n")

    rows = ledger.query(
        """SELECT routed_model, COUNT(*), SUM(input_tokens), SUM(output_tokens),
                  SUM(cost_usd), SUM(cost_usd IS NULL)
           FROM requests WHERE ts >= ? GROUP BY routed_model ORDER BY SUM(cost_usd) DESC""",
        (since,),
    )
    print(f"{'model':<24}{'calls':>7}{'in tok':>12}{'out tok':>12}{'cost':>12}{'unpriced':>10}")
    for model, n, i, o, cost, unpriced in rows:
        print(f"{model:<24}{n:>7}{i or 0:>12,}{o or 0:>12,}{fmt_cost(cost):>12}{unpriced:>10}")

    print("\n-- by rule --")
    for rule, n, cost in ledger.query(
        "SELECT rule, COUNT(*), SUM(cost_usd) FROM requests "
        "WHERE ts >= ? GROUP BY rule ORDER BY 2 DESC",
        (since,),
    ):
        print(f"  {rule:<28}{n:>6} calls   {fmt_cost(cost)}")

    # Downgrade candidates: frontier calls with tiny workloads and no explicit tag.
    q_marks = ",".join("?" for _ in FRONTIER)
    downs = ledger.query(
        f"""SELECT id, routed_model, rule, input_tokens, output_tokens, cost_usd
            FROM requests
            WHERE ts >= ? AND routed_model IN ({q_marks})
              AND (tag IS NULL OR tag = '')
              AND COALESCE(input_tokens, est_input_tokens) < 4000
              AND COALESCE(output_tokens, 0) < 800
            ORDER BY cost_usd DESC LIMIT 15""",
        (since, *FRONTIER),
    )
    print(f"\n-- downgrade candidates ({len(downs)}) --")
    print("   frontier calls that looked like daily-driver work:")
    for rid, model, rule, i, o, cost in downs:
        print(f"   #{rid} {model} via {rule}: {i or '?'} in / {o or '?'} out, {fmt_cost(cost)}")

    # Escalation candidates: rapid same-size retries on cheap models.
    q_marks = ",".join("?" for _ in CHEAP)
    escs = ledger.query(
        f"""SELECT a.id, a.routed_model, a.est_input_tokens
            FROM requests a JOIN requests b
              ON b.ts > a.ts AND b.ts - a.ts < 180
             AND b.harness = a.harness
             AND ABS(b.est_input_tokens - a.est_input_tokens) < 200
            WHERE a.ts >= ? AND a.routed_model IN ({q_marks})
            GROUP BY a.id LIMIT 15""",
        (since, *CHEAP),
    )
    print(f"\n-- escalation candidates ({len(escs)}) --")
    print("   cheap-model calls followed by a near-identical retry within 3 min:")
    for rid, model, est in escs:
        print(f"   #{rid} {model} (~{est} tok) — consider routing this shape up a tier")


if __name__ == "__main__":
    main()
