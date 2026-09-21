#!/usr/bin/env python3
"""traffic-guard — сторож трафика нод у хостера: сколько сожгли, сколько будет к концу месяца.

Зачем. Лимит трафика у ноды в Remnawave — это только уведомление, отсечки нет. А счётчик
панели (trafficUsedBytes) считает трафик ПОЛЬЗОВАТЕЛЕЙ, хостер же выставляет счёт по
интерфейсу — сырой счётчик одного направления на ~12–15% больше. При квоте 32 ТБ эти
проценты — разница между «всё спокойно» и перерасходом, о котором узнаёшь из счёта.

Поэтому считаем сами: берём СЫРЫЕ счётчики интерфейса ноды из панели (rxTotal/txTotal),
копим месячный расход в своём состоянии (счётчики сбрасываются при перезагрузке сервера —
это учтено) и сравниваем с включённым объёмом: max(rx, tx) или rx+tx — как считает
хостер (traffic_guard.billing). Пороги 70/85/95% — по ПРОГНОЗУ на конец месяца (с
четвёртого дня учёта), перерасход по прогнозу — отдельным письмом. Одно письмо на порог
за месяц. Учёт может начаться позже сброса счётчика — прогноз считается с начала учёта.

Включённый объём: traffic_guard.overrides[имя] → лимит ноды в панели (trafficLimitBytes)
→ traffic_guard.included_tb_default. Ноды без объёма пропускаются.

  traffic-guard                 # накопить счётчики, при пороге — Telegram (таймер раз в 30 мин)
  traffic-guard --report        # таблица по всем нодам: израсходовано, прогноз, %
  traffic-guard --force         # таблицу — в Telegram
"""
import argparse
import calendar
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guards_common import Panel, common_args, load_config, load_state, node_metrics, save_state, telegram_send  # noqa: E402

DEFAULTS = {"included_tb_default": 0, "overrides": {}, "marks": [70, 85, 95], "billing": "max"}


def accumulate(state, name, period, rx_total, tx_total, now_utc):
    """Копим месячный расход по сырым счётчикам; счётчик упал (перезагрузка) — прибавляем текущее."""
    rec = state.setdefault(name, {})
    if rec.get("period") != period:
        rec.update({"period": period, "rx": 0, "tx": 0, "last_rx": rx_total, "last_tx": tx_total,
                    "started_at": now_utc.strftime("%Y-%m-%d %H:%M:%S"), "alerts": []})
        return rec
    rec["rx"] += (rx_total - rec["last_rx"]) if rx_total >= rec["last_rx"] else rx_total
    rec["tx"] += (tx_total - rec["last_tx"]) if tx_total >= rec["last_tx"] else tx_total
    rec["last_rx"], rec["last_tx"] = rx_total, tx_total
    return rec


def main():
    ap = common_args(argparse.ArgumentParser(description="traffic-guard — трафик нод у хостера с прогнозом"))
    ap.add_argument("--report", action="store_true", help="напечатать таблицу по всем нодам")
    a = ap.parse_args()
    cfg = load_config(a.config)
    tg_cfg = {**DEFAULTS, **cfg.get("traffic_guard", {})}
    billing = str(tg_cfg["billing"]).lower()
    marks = sorted(int(m) for m in tg_cfg["marks"])
    panel = Panel(cfg["panel"]["url"], cfg["panel"]["token"])
    state = load_state(cfg, "traffic.json")
    now_utc = dt.datetime.now(dt.timezone.utc)
    now = dt.datetime.now()
    period = now.strftime("%Y-%m")
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    rows, fired_msgs = [], []

    for n in sorted(panel.nodes(), key=lambda x: x.get("name") or ""):
        name = n.get("name")
        if not name or n.get("isDisabled"):
            continue
        m = node_metrics(n)
        if m["rx_total"] <= 0 and m["tx_total"] <= 0:
            continue
        panel_limit_tb = float(n.get("trafficLimitBytes") or 0) / 1e12
        included = float(tg_cfg["overrides"].get(name) or panel_limit_tb or tg_cfg["included_tb_default"] or 0)
        rec = accumulate(state, name, period, m["rx_total"], m["tx_total"], now_utc)
        if included <= 0:
            continue
        billed_tb = ((rec["rx"] + rec["tx"]) if billing == "sum" else max(rec["rx"], rec["tx"])) / 1e12
        reset_day = int(n.get("trafficResetDay") or 1)
        elapsed = now.day - reset_day + 1
        if elapsed <= 0:
            elapsed += days_in_month
        elapsed = float(max(elapsed, 1))
        started_note = ""
        try:
            started = dt.datetime.strptime(rec["started_at"], "%Y-%m-%d %H:%M:%S")
            since = (now_utc.replace(tzinfo=None) - started).total_seconds() / 86400
            if 0 < since < elapsed - 1:
                started_note = f" · учёт с {started:%d.%m}, первые дни месяца не вошли"
                elapsed = max(since, 1.0)
        except (ValueError, TypeError):
            pass
        percent = billed_tb / included * 100
        projected = billed_tb / elapsed * days_in_month
        projected_pct = projected / included * 100
        over = max(projected - included, 0.0)
        days_left = max(days_in_month - now.day, 0)
        rows.append(f"{name:<16} {billed_tb:5.1f}/{included:.0f} ТБ {percent:3.0f}%  прогноз {projected:5.1f} ТБ ({projected_pct:.0f}%)")
        if billed_tb <= 0:
            continue
        eff_pct = max(percent, projected_pct) if elapsed >= 3 else percent
        sent = rec.setdefault("alerts", [])
        fired = None
        if elapsed >= 3 and over > 0 and "forecast" not in sent:
            fired = ("forecast", f"прогноз перерасхода, {projected_pct:.0f}%")
            sent.extend(str(x) for x in marks if str(x) not in sent)
        if fired is None:
            crossed = [x for x in marks if eff_pct >= x]
            if crossed:
                top = max(crossed)
                if str(top) not in sent:
                    fired = (str(top), f"порог {top}% по прогнозу" if projected_pct >= top > percent else f"порог {top}%")
                    sent.extend(str(x) for x in crossed if str(x) not in sent)
        if not fired:
            continue
        key, reason = fired
        if key not in sent:
            sent.append(key)
        reason += (" · сумма входа и выхода" if billing == "sum" else "") + started_note
        fired_msgs.append(
            f"📊 <b>Трафик ноды: {reason}</b>\n\n🎯 <b>Нода:</b> {name} ({n.get('address')})\n"
            f"📦 <b>Израсходовано:</b> {billed_tb:.1f} ТБ из {included:.0f} ТБ ({percent:.0f}%)\n"
            f"📈 <b>Прогноз к концу месяца:</b> {projected:.1f} ТБ\n💸 <b>Перерасход по прогнозу:</b> {over:.1f} ТБ\n"
            f"🗓 <b>До сброса счётчика:</b> {days_left} дн.\n\n"
            f"Что делать: увести часть трафика на другую ноду, поднять объём у хостера или дождаться сброса.")

    save_state(cfg, "traffic.json", state)
    head = f"трафик за {period} ({'сумма' if billing == 'sum' else 'max направления'}):"
    if a.report or a.force:
        print(head)
        print("\n".join("  " + r for r in rows) if rows else "  нет нод с включённым объёмом (overrides / лимит в панели / included_tb_default)")
    for msg in fired_msgs:
        print("\n" + msg.replace("<b>", "").replace("</b>", ""))
        telegram_send(cfg["telegram"], msg, a.quiet)
    if a.force and rows:
        telegram_send(cfg["telegram"], "📊 <b>" + head + "</b>\n<pre>" + "\n".join(rows) + "</pre>", a.quiet)
    if not (a.report or a.force or fired_msgs):
        print(f"счётчики обновлены ({len(rows)} нод), порогов не пересечено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
