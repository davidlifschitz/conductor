"""Rendering: plain data in, rich renderables (or plain strings) out.

No I/O, no sqlite, no sleeping — every function is unit-testable by asserting
on rendered text (rich Console(record=True) or the plain-string helpers).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .data import Health, RequestRow, Summary

if TYPE_CHECKING:
    pass


def fmt_cost(c: float | None) -> str:
    """'$0.0123' or '?' — identical semantics to report.fmt_cost."""
    return f"${c:,.4f}" if c is not None else "?"


def fmt_tokens(n: int | None) -> str:
    """'12,345' or '?' when None."""
    return f"{n:,}" if n is not None else "?"


def fmt_clock(ts: float) -> str:
    """Local 'HH:MM:SS' from an epoch float."""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def fmt_latency(ms: int | None) -> str:
    """'842ms' / '12.3s' / '?'."""
    if ms is None:
        return "?"
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def short(s: str | None, width: int) -> str:
    """Truncate with a trailing ellipsis; '' for None."""
    if s is None:
        return ""
    if len(s) <= width:
        return s
    if width <= 1:
        return "…"[:width]
    return s[: width - 1] + "…"


def row_style(row: RequestRow) -> str:
    """rich style string for a tail row: 'yellow' when row.escalated,
    'red' when status not 200/None-status, '' otherwise."""
    if row.escalated:
        return "yellow"
    if row.status is None or row.status != 200:
        return "red"
    return ""


def _model_arrow(row: RequestRow) -> str:
    req = short(row.requested_model, 18) if row.requested_model else "?"
    routed = short(row.routed_model, 22) if row.routed_model else "?"
    # Prefer short aliases for common prefixes in the arrow display
    req_short = _short_model(row.requested_model)
    routed_short = row.routed_model or "?"
    return f"{req_short}→{routed_short}"


def _short_model(model: str | None) -> str:
    if not model:
        return "?"
    # Strip common claude- prefix for the left side of req→routed
    for prefix in ("claude-",):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


def detail_text(row: RequestRow) -> str:
    """Plain multi-line string for `show` (mockup §5.4). Also reused by
    tests since it's terminal-independent."""
    local = datetime.fromtimestamp(row.ts).strftime("%Y-%m-%d %H:%M:%S")
    harness = row.harness if row.harness is not None else "—"
    tag = row.tag if row.tag is not None else "—"
    rule = row.rule if row.rule is not None else "—"
    if row.escalated:
        rule = f"{rule}        ← escalation retry"
    req = row.requested_model if row.requested_model is not None else "—"
    routed = row.routed_model if row.routed_model is not None else "—"
    stream = "yes" if row.stream else "no" if row.stream is not None else "—"
    status = str(row.status) if row.status is not None else "?"
    latency = f"{row.latency_ms} ms" if row.latency_ms is not None else "?"
    in_tok = fmt_tokens(row.input_tokens)
    out_tok = fmt_tokens(row.output_tokens)
    est = fmt_tokens(row.est_input_tokens)
    tokens = f"{in_tok} in / {out_tok} out (est. input at routing: {est})"
    cost = fmt_cost(row.cost_usd)

    lines = [
        f"request #{row.id}",
        f"  time              {local} (local)",
        f"  harness           {harness}",
        f"  tag               {tag}",
        f"  rule              {rule}",
        f"  requested model   {req}",
        f"  routed model      {routed}",
        f"  stream            {stream}",
        f"  status            {status}",
        f"  latency           {latency}",
        f"  tokens            {tokens}",
        f"  cost              {cost}",
    ]
    return "\n".join(lines)


def stats_text(summary: Summary, days: int) -> str:
    """Plain-text version of the summary for the `stats` subcommand
    (renders the same tables via a non-live rich Console)."""
    console = Console(record=True, width=100, force_terminal=False, soft_wrap=True)
    # Empty window: show $0.0000 rather than '?' (edge case §6).
    cost_display = (
        fmt_cost(0.0)
        if summary.total_calls == 0 and summary.total_cost is None
        else fmt_cost(summary.total_cost)
    )
    console.print(f"== conductor stats: last {days} day(s) ==")
    console.print()
    console.print(
        f"calls: {summary.total_calls:,}   cost: {cost_display}   "
        f"escalations: {summary.escalation_count}   errors: {summary.error_count}"
    )
    console.print()

    console.print("by model")
    console.print(
        f"  {'model':<24}{'calls':>7}{'in tok':>12}{'out tok':>12}{'cost':>12}{'unpriced':>10}"
    )
    if not summary.by_model:
        console.print("  no requests yet")
    else:
        for model, calls, in_tok, out_tok, cost, unpriced in summary.by_model:
            console.print(
                f"  {str(model or '?'):<24}{calls:>7}{in_tok:>12,}{out_tok:>12,}"
                f"{fmt_cost(cost):>12}{unpriced:>10}"
            )

    console.print()
    console.print("by rule")
    console.print(f"  {'rule':<28}{'calls':>7}{'cost':>12}")
    if not summary.by_rule:
        console.print("  no requests yet")
    else:
        for rule, calls, cost in summary.by_rule:
            console.print(
                f"  {str(rule or '?'):<28}{calls:>7}{fmt_cost(cost):>12}"
            )

    return console.export_text()


def tail_table(rows: list[RequestRow], max_rows: int) -> Table:
    """The live-tail table (columns per mockup §5.1). Shows the LAST max_rows
    entries. Escalated rows get a '⤴' marker in the rule column plus yellow
    style; error rows red."""
    table = Table(title="requests (newest last)", expand=True, pad_edge=False)
    table.add_column("id", justify="right", no_wrap=True)
    table.add_column("time", no_wrap=True)
    table.add_column("harness", no_wrap=True)
    table.add_column("rule", no_wrap=True)
    table.add_column("req→routed model", no_wrap=True)
    table.add_column("tok in/out", justify="right", no_wrap=True)
    table.add_column("cost", justify="right", no_wrap=True)
    table.add_column("ms", justify="right", no_wrap=True)

    display = rows[-max_rows:] if max_rows > 0 else rows
    if not display:
        table.add_row("", "", "", "no requests yet", "", "", "", "", style="dim")
        return table

    for row in display:
        rule = short(row.rule, 20)
        if row.escalated:
            rule = f"⤴ {rule}"
        style = row_style(row)
        table.add_row(
            str(row.id),
            fmt_clock(row.ts),
            short(row.harness, 16),
            rule,
            short(_model_arrow(row), 36),
            f"{fmt_tokens(row.input_tokens)}/{fmt_tokens(row.output_tokens)}",
            fmt_cost(row.cost_usd),
            fmt_latency(row.latency_ms),
            style=style or None,
        )
    return table


def summary_panels(summary: Summary, days: int) -> Group:
    """Two side-by-side tables ('by model', 'by rule') plus a totals line."""
    totals = Text(
        f"  last {days} day{'s' if days != 1 else ''}        "
        f"calls: {summary.total_calls}   cost: {fmt_cost(summary.total_cost)}   "
        f"escalations: {summary.escalation_count}   errors: {summary.error_count}"
    )

    model_table = Table(show_header=True, expand=True, box=None, pad_edge=False)
    model_table.add_column("model", overflow="ellipsis")
    model_table.add_column("calls", justify="right")
    model_table.add_column("cost", justify="right")
    model_table.add_column("unprc", justify="right")
    if not summary.by_model:
        model_table.add_row("no requests yet", "", "", "", style="dim")
    else:
        for model, calls, _i, _o, cost, unpriced in summary.by_model:
            model_table.add_row(
                str(model or "?"),
                str(calls),
                fmt_cost(cost),
                str(unpriced),
            )

    rule_table = Table(show_header=True, expand=True, box=None, pad_edge=False)
    rule_table.add_column("rule", overflow="ellipsis")
    rule_table.add_column("calls", justify="right")
    rule_table.add_column("cost", justify="right")
    if not summary.by_rule:
        rule_table.add_row("no requests yet", "", "", style="dim")
    else:
        for rule, calls, cost in summary.by_rule:
            rule_table.add_row(str(rule or "?"), str(calls), fmt_cost(cost))

    from rich.columns import Columns

    panels = Columns(
        [
            Panel(model_table, title="by model", expand=True),
            Panel(rule_table, title="by rule", expand=True),
        ],
        equal=True,
        expand=True,
    )
    return Group(totals, panels)


def header_bar(
    health: Health,
    ladder: list[str],
    db_path: str,
    paused: bool,
    warn: str | None,
) -> Text:
    """One-line status: proxy UP/DOWN, default model, ladder, db path,
    PAUSED flag, and any per-tick warning (e.g. 'db locked, retrying')."""
    t = Text()
    t.append(" conductor ", style="bold")
    if health.up:
        t.append("⏵ proxy UP", style="green")
        if health.default_model:
            t.append(f"  default={health.default_model}")
    else:
        err = health.error or "unknown"
        t.append(f"⏵ proxy DOWN ({err})", style="red")

    if ladder:
        # Shorten ladder display: strip claude- prefix for readability
        short_ladder = " → ".join(_short_model(m) for m in ladder)
        t.append(f"  ladder: {short_ladder}")

    from pathlib import Path

    t.append(f"   db: {Path(db_path).name}")

    if paused:
        t.append("  PAUSED", style="yellow bold")
    if warn:
        t.append(f"  {warn}", style="yellow")

    return t


def plain_tail_line(row: RequestRow) -> str:
    """One plain-text line for the `tail` subcommand (mockup §5.3)."""
    status = str(row.status) if row.status is not None else "?"
    return (
        f"{row.id:>5}  {fmt_clock(row.ts):<8}  "
        f"{short(row.harness, 15):<15}  "
        f"{short(row.rule, 20):<20}  "
        f"{_model_arrow(row):<28}  "
        f"{fmt_tokens(row.input_tokens)}/{fmt_tokens(row.output_tokens):>6}  "
        f"{fmt_cost(row.cost_usd):>9}  "
        f"{row.latency_ms if row.latency_ms is not None else '?':>5}  "
        f"{status:>3}"
    )


def plain_tail_header() -> str:
    return (
        f"{'id':>5}  {'time':<8}  {'harness':<15}  {'rule':<20}  "
        f"{'req→routed':<28}  {'in/out':>13}  {'cost':>9}  {'ms':>5}  {'st':>3}"
    )
