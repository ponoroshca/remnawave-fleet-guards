#!/usr/bin/env python3
"""Логика сторожа трафика на искусственных датах — без панели. Запуск: python3 tests/test_traffic_logic.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from guards_common import _split_message, esc  # noqa: E402
from traffic_guard import accumulate, billing_period  # noqa: E402

D = dt.date
cases = [
    ("сброс 1-го: сентябрь", billing_period(D(2026, 9, 21), 1)[:2], (D(2026, 9, 1), D(2026, 10, 1))),
    ("сброс 15-го, сегодня 21.09", billing_period(D(2026, 9, 21), 15)[:2], (D(2026, 9, 15), D(2026, 10, 15))),
    ("сброс 15-го, сегодня 10.09", billing_period(D(2026, 9, 10), 15)[:2], (D(2026, 8, 15), D(2026, 9, 15))),
    ("январь, сброс 27-го", billing_period(D(2026, 1, 5), 27)[:2], (D(2025, 12, 27), D(2026, 1, 27))),
    ("27.02, сброс 31-го", billing_period(D(2026, 2, 27), 31)[:2], (D(2026, 1, 31), D(2026, 2, 28))),
    ("28.02 — день сброса при 31-м", billing_period(D(2026, 2, 28), 31)[:2], (D(2026, 2, 28), D(2026, 3, 31))),
    ("31 декабря, сброс 1-го", billing_period(D(2026, 12, 31), 1)[:2], (D(2026, 12, 1), D(2027, 1, 1))),
    ("reset_day None → 1-е", billing_period(D(2026, 9, 21), None)[:2], (D(2026, 9, 1), D(2026, 10, 1))),
]
st, now = {}, dt.datetime(2026, 9, 21, 10, 0)
r = accumulate(st, "N", "2026-09-01", 1000, 2000, now); cases.append(("первый замер", (r["rx"], r["tx"], r["last_rx"]), (0, 0, 1000)))
r = accumulate(st, "N", "2026-09-01", 1500, 2600, now); cases.append(("рост счётчиков", (r["rx"], r["tx"]), (500, 600)))
r = accumulate(st, "N", "2026-09-01", 100, 50, now); cases.append(("перезагрузка: счётчик упал", (r["rx"], r["tx"]), (600, 650)))
r = accumulate(st, "N", "2026-10-01", 900, 900, now); cases.append(("новый период", (r["rx"], r["tx"], r["alerts"]), (0, 0, [])))
long = "<b>x</b>\n" + "\n".join("🔴 строка тревоги номер %d" % i for i in range(120)) + "\n<pre>" + "\n".join("node-%d  100 Мбит" % i for i in range(120)) + "</pre>"
parts = _split_message(long)
cases.append(("длинное сообщение режется", (len(parts) > 1, max(len(p) for p in parts) <= 4000, all(p.count("<pre>") == p.count("</pre>") for p in parts)), (True, True, True)))
cases.append(("экранирование HTML", esc("a<b>&c"), "a&lt;b&gt;&amp;c"))
bad = 0
for name, got, want in cases:
    ok = got == want
    bad += not ok
    print(("  ✅ " if ok else "  🔴 ") + name + ("" if ok else f"  получено {got}, ожидалось {want}"))
print(f"\n{'всё в порядке' if not bad else str(bad) + ' ошибок'}")
sys.exit(1 if bad else 0)
