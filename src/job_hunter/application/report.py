"""Формирование ранжированного отчёта (Markdown)."""

from collections.abc import Sequence
from datetime import UTC, datetime

from job_hunter.application.analyze import Assessment
from job_hunter.domain.models import Salary, VacancyRecord
from job_hunter.domain.profile import CandidateProfile


def _fmt_salary(salary: Salary | None) -> str:
    if salary is None:
        return "—"
    left = f"{salary.min_amount:,}".replace(",", " ") if salary.min_amount else "…"
    right = f"{salary.max_amount:,}".replace(",", " ") if salary.max_amount else "…"
    currency = f" {salary.currency}" if salary.currency else ""
    return f"{left}–{right}{currency}"


def _fmt_age(record: VacancyRecord, now: datetime) -> str:
    base = record.vacancy.published_at or record.first_seen_at
    return f"{max(0, (now - base).days)} дн"


def render_markdown(
    ranked: Sequence[tuple[VacancyRecord, Assessment]],
    profile: CandidateProfile | None,
    *,
    total_in_base: int,
    rejected_count: int,
    triage_dropped: int = 0,
    unvetted_tail: int = 0,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    extra = ""
    if triage_dropped:
        extra += f" · отсеяно LLM-триажем: {triage_dropped}"
    if unvetted_tail:
        extra += f" · без триажа (вне пула): {unvetted_tail}"
    lines = [
        f"# Job Hunter — ранжированный список вакансий от {now.date().isoformat()}",
        "",
        (
            f"В базе: {total_in_base} · отбраковано по deal-breakers: {rejected_count}"
            f"{extra} · в отчёте: {len(ranked)}"
        ),
    ]
    if profile:
        roles = ", ".join(profile.desired_roles) or "—"
        skills = ", ".join(profile.skills) or "—"
        lines.append(f"Профиль: роли — {roles}; навыки — {skills}")
    lines.append("")

    lines.append("| # | Балл | Вакансия | Компания | Зарплата | Возраст | Источник |")
    lines.append("|---|---|---|---|---|---|---|")
    for idx, (record, assessment) in enumerate(ranked, start=1):
        v = record.vacancy
        lines.append(
            f"| {idx} | **{assessment.total}** | [{v.title}]({v.url}) | {v.employer.name} "
            f"| {_fmt_salary(v.salary)} | {_fmt_age(record, now)} | {v.source} |"
        )
    lines.append("")

    lines.append("## Почему такие баллы")
    for idx, (record, assessment) in enumerate(ranked, start=1):
        v = record.vacancy
        lines.append(f"### {idx}. {v.title} — {v.employer.name} ({assessment.total})")
        for signal in sorted(assessment.signals, key=lambda s: s.points, reverse=True):
            sign = "+" if signal.points >= 0 else ""
            lines.append(f"- {sign}{signal.points} — {signal.reason}")
        lines.append("")
    return "\n".join(lines)
