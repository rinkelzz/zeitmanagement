"""Wiederkehrende Aufgaben.

Regeln (Spalte tasks.recur):
  d:N  alle N Tage          wd   werktags (Mo–Fr)
  w:N  alle N Wochen        m:N  alle N Monate (gleicher Tag, am Monatsende gekürzt)
  y:N  alle N Jahre
Monatliche und jährliche Regeln merken sich den Monatstag („m:1@31“), damit nach einem kurzen Monat
wieder der 31. kommt und nicht dauerhaft der 28.
Wird eine wiederkehrende Aufgabe erledigt, entsteht die nächste – mit dem nächsten Termin, der nach heute liegt.
"""
import calendar
import re
from datetime import date, timedelta
from typing import Optional

RULE_RE = re.compile(r"^(?:wd|[dw]:[1-9]\d?|[my]:[1-9]\d?(?:@(?:[1-9]|[12]\d|3[01]))?)$")
UNITS = {"d": ("Tag", "Tage"), "w": ("Woche", "Wochen"), "m": ("Monat", "Monate"), "y": ("Jahr", "Jahre")}
SIMPLE = {"d:1": "täglich", "wd": "werktags", "w:1": "wöchentlich", "m:1": "monatlich", "y:1": "jährlich"}


def valid(rule: Optional[str]) -> bool:
    return rule is None or bool(RULE_RE.match(rule))


def base(rule: str) -> str:
    return rule.split("@")[0]


def with_anchor(rule: Optional[str], due: Optional[date]) -> Optional[str]:
    """Monatstag an monatliche/jährliche Regeln hängen (aus der Fälligkeit)."""
    if not rule or not due or rule[0] not in "my":
        return rule
    return f"{base(rule)}@{due.day}"


def label(rule: Optional[str]) -> str:
    if not rule:
        return ""
    rule = base(rule)
    if rule in SIMPLE:
        return SIMPLE[rule]
    unit, n = rule.split(":")
    return f"alle {n} {UNITS[unit][1]}"


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def step(d: date, rule: str, anchor_day: Optional[int] = None) -> date:
    """Ein Schritt weiter. anchor_day: ursprünglicher Monatstag (31. bleibt nach dem Februar der 31.)."""
    if "@" in rule:
        rule, a = rule.split("@")
        anchor_day = anchor_day or int(a)
    if rule == "wd":
        d += timedelta(days=1)
        while d.weekday() > 4:
            d += timedelta(days=1)
        return d
    unit, n = rule.split(":")
    n = int(n)
    if unit == "d":
        return d + timedelta(days=n)
    if unit == "w":
        return d + timedelta(weeks=n)
    nd = _add_months(d, n if unit == "m" else 12 * n)
    if anchor_day:
        nd = nd.replace(day=min(anchor_day, calendar.monthrange(nd.year, nd.month)[1]))
    return nd


def next_due(due: date, rule: str, today: date) -> date:
    """Nächster Termin nach dem bisherigen, und nicht in der Vergangenheit."""
    anchor = int(rule.split("@")[1]) if "@" in rule else due.day
    d = step(due, rule, anchor)
    for _ in range(5000):
        if d > today:
            return d
        d = step(d, rule, anchor)
    return today + timedelta(days=1)
