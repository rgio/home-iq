"""Match-rate and quarantine reporting against the spec §4 acceptance
criterion: >=90% of residential alteration permits 2015-present resolve to
exactly one PIN. The quarantine rate itself is a number worth reporting,
not just a debug artifact — spec's words, not ours."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import Engine, text

ACCEPTANCE_THRESHOLD = 0.90


@dataclass
class MatchReport:
    total_permits: int
    matched: int
    quarantined: int
    quarantine_by_reason: dict[str, int] = field(default_factory=dict)
    match_method_counts: dict[str, int] = field(default_factory=dict)

    @property
    def match_rate(self) -> float:
        return self.matched / self.total_permits if self.total_permits else 0.0

    @property
    def meets_acceptance_criterion(self) -> bool:
        return self.match_rate >= ACCEPTANCE_THRESHOLD


def compute_match_report(engine: Engine) -> MatchReport:
    with engine.connect() as conn:
        total = conn.execute(text("SELECT count(*) FROM permits")).scalar_one()
        matched = conn.execute(text("SELECT count(*) FROM permit_pin_match")).scalar_one()
        quarantined = conn.execute(text("SELECT count(*) FROM permit_quarantine")).scalar_one()
        reason_rows = conn.execute(text(
            "SELECT reason, count(*) FROM permit_quarantine GROUP BY reason ORDER BY count(*) DESC"
        )).all()
        method_rows = conn.execute(text(
            "SELECT match_method, count(*) FROM permit_pin_match GROUP BY match_method"
        )).all()

    return MatchReport(
        total_permits=total,
        matched=matched,
        quarantined=quarantined,
        quarantine_by_reason=dict(reason_rows),
        match_method_counts=dict(method_rows),
    )


def format_report(report: MatchReport) -> str:
    lines = [
        "=== HomeIQ P0 permit -> PIN join report ===",
        f"Total permits:        {report.total_permits}",
        f"Matched:              {report.matched} ({report.match_rate:.1%})",
        f"Quarantined:          {report.quarantined} ({1 - report.match_rate:.1%})",
        f"Acceptance criterion: >= {ACCEPTANCE_THRESHOLD:.0%} match rate — "
        f"{'PASS' if report.meets_acceptance_criterion else 'FAIL'}",
        "",
        "Match method breakdown:",
    ]
    for method, count in sorted(report.match_method_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {method:<20} {count}")
    lines.append("")
    lines.append("Quarantine reason breakdown:")
    for reason, count in sorted(report.quarantine_by_reason.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {reason:<24} {count}")
    return "\n".join(lines)


def quarantine_detail_frame(engine: Engine) -> pd.DataFrame:
    """Full quarantine rows joined back to the permit for manual review."""
    query = text(
        "SELECT q.permitnum, q.reason, q.detail, p.originaladdress1, p.originalzip, "
        "p.description, p.statuscurrent "
        "FROM permit_quarantine q JOIN permits p ON p.permitnum = q.permitnum "
        "ORDER BY q.reason, q.permitnum"
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn)
