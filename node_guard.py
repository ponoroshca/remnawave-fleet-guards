#!/usr/bin/env python3
"""node-guard — сторож нод: перегруз, память, полоса и «нода в маршрутизации, но молчит».

Зачем. Балансир xray меряет только задержку и не знает про полосу и ядра — перегруз он не
предотвратит; страна-режимы и резервные хосты вообще прибиты к одной ноде. А самое
коварное: ноду заблокировали по IP из страны клиентов — для панели она ЖИВА (на связи,
load 0, трафик 0, ни один порог не превышен). Ловится иначе: нода числится в балансире
или в правиле маршрутизации моста, то есть ОБЯЗАНА нести трафик, а несёт ноль, пока
остальной флот загружен.

Проверки (пороги — в конфиге node_guard.*):
  • нода отвалилась от панели;
  • load на ядро ≥ load_per_core (0.80);
  • свободной памяти < mem_free_min (12%);
  • полоса ≥ bw_share (70%) от ёмкости канала (capacity_mbit, если задана);
  • 🔴 в маршрутизации моста, но молчит (< silent_mbit и 0 юзеров), пока флот несёт ≥ fleet_busy_mbit;
    parked — отставлены осознанно (не тревога), alt_ips — вторые адреса нод, chains — цепочки
    «вход → выход» (вход занят, выход молчит = разрыв).

Только читает панель. Пишет в Telegram, ТОЛЬКО если что-то нашлось (или --force).

  node-guard                    # тихо, если всё хорошо (так его запускает таймер)
  node-guard --force            # отчёт по всем нодам в Telegram в любом случае
  node-guard --force --quiet    # только в консоль
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guards_common import Panel, common_args, esc, load_config, node_metrics, telegram_send  # noqa: E402

DEFAULTS = {"load_per_core": 0.80, "mem_free_min": 0.12, "bw_share": 0.70, "silent_mbit": 1.0, "fleet_busy_mbit": 30.0,
            "capacity_mbit": {}, "alt_ips": {}, "parked": {}, "bridge_profiles": [], "chains": []}


def routing_targets(panel, cfg_ng, nodes):
    """адрес ноды → за что отвечает (балансир / правило) по профилям мостов."""
    expected, warnings = {}, []
    node_addrs = {n.get("address") for n in nodes}
    alt = cfg_ng["alt_ips"]
    for pname in cfg_ng["bridge_profiles"]:
        prof = panel.profile(pname)
        if not prof:
            warnings.append(f"🟠 профиль мостов «{pname}» не найден в панели — проверка «молчит» по нему пропущена")
            continue
        conf = prof.get("config") or {}
        ob_addr = {}
        for o in conf.get("outbounds", []):
            try:
                ob_addr[o["tag"]] = o["settings"]["vnext"][0]["address"]
            except (KeyError, IndexError, TypeError):
                pass
        rt = conf.get("routing") or {}
        for b in rt.get("balancers") or []:
            for tag in b.get("selector") or []:
                if tag in ob_addr:
                    expected.setdefault(alt.get(ob_addr[tag], ob_addr[tag]), []).append(f"балансир «{b.get('tag')}»")
        for r in rt.get("rules") or []:
            tag = r.get("outboundTag")
            inb = ", ".join(r.get("inboundTag") or [])
            if tag in ob_addr and inb:
                expected.setdefault(alt.get(ob_addr[tag], ob_addr[tag]), []).append(inb)
    for a in sorted(set(expected) - node_addrs):
        warnings.append(f"🟠 outbound моста смотрит на {a} — такого адреса нет ни у одной ноды. Если это второй IP ноды, "
                        f"добавьте его в node_guard.alt_ips, иначе проверка «молчит» её не покрывает.")
    return expected, warnings


def main():
    ap = common_args(argparse.ArgumentParser(description="node-guard — сторож нод Remnawave"))
    a = ap.parse_args()
    cfg = load_config(a.config)
    ng = {**DEFAULTS, **cfg.get("node_guard", {})}
    panel = Panel(cfg["panel"]["url"], cfg["panel"]["token"])
    nodes = panel.nodes()
    expected, alerts = routing_targets(panel, ng, nodes)
    table, silent, parked_seen = [], [], []
    fleet_mbit = 0.0
    by_addr = {n.get("address"): n for n in nodes}

    for n in sorted(nodes, key=lambda x: x.get("name") or ""):
        name, addr = n.get("name"), n.get("address")
        if n.get("isDisabled"):
            continue
        if not n.get("isConnected"):
            alerts.append(f"🔴 {name} ({addr}) — ОТВАЛИЛАСЬ от панели")
            table.append(f"{name:<16} ОТВАЛИЛАСЬ")
            continue
        m = node_metrics(n)
        fleet_mbit += m["mbit"]
        cap = ng["capacity_mbit"].get(name)
        roles = expected.get(addr)
        table.append(f"{name:<16} {m['mbit']:>5.0f} Мбит  load {m['load']:.2f}/{m['cpus']}я={m['per_core']:.2f}"
                     f"  ОЗУ своб {m['mem_free_share'] * 100:.0f}%  юзеров {m['users']}{' ←в маршруте' if roles else ''}")
        if m["per_core"] >= ng["load_per_core"]:
            alerts.append(f"🟠 {name} ({addr}) — CPU: load {m['load']:.2f} на {m['cpus']} ядрах = {m['per_core']:.2f} на ядро (порог {ng['load_per_core']})")
        if m["mem_free_share"] < ng["mem_free_min"]:
            alerts.append(f"🟠 {name} — свободно всего {m['mem_free_share'] * 100:.0f}% памяти")
        if cap and m["mbit"] / cap >= ng["bw_share"]:
            alerts.append(f"🟠 {name} — полоса {m['mbit']:.0f} из {cap} Мбит ({m['mbit'] / cap * 100:.0f}% канала)")
        if roles and m["mbit"] < ng["silent_mbit"] and not m["users"]:
            why = ng["parked"].get(addr) or ng["parked"].get(name)
            if why:
                parked_seen.append(f"{name}: {why}")
            else:
                silent.append((name, addr, sorted(set(roles))))

    # цепочки «вход → выход»: на входе люди есть, выход молчит — разрыв, который панель не видит
    for ch in ng["chains"]:
        entry, ex = by_addr.get(ch.get("entry")), by_addr.get(ch.get("exit"))
        if not entry or not ex:
            alerts.append(f"🟠 цепочка «{ch.get('name')}»: вход или выход не найдены среди нод панели")
            continue
        if not entry.get("isConnected"):
            alerts.append(f"🔴 цепочка «{ch.get('name')}»: вход {ch['entry']} отвалился — клиенты не подключатся")
        if not ex.get("isConnected"):
            alerts.append(f"🔴 цепочка «{ch.get('name')}»: выход {ch['exit']} отвалился от панели")
        eu, wm = node_metrics(entry)["users"], node_metrics(ex)["mbit"]
        if entry.get("isConnected") and ex.get("isConnected") and eu >= ch.get("min_entry_users", 10) and wm < ch.get("min_exit_mbit", 0.3):
            alerts.append(f"🟡 цепочка «{ch.get('name')}», подозрение: на входе {ch['entry']} {eu} чел., а выход {ch['exit']} несёт {wm:.1f} Мбит. "
                          f"Метрики панели запаздывают — проверьте на самом выходе соединения (ss -tn state established) до выводов.")

    if fleet_mbit >= ng["fleet_busy_mbit"]:
        for name, addr, roles in silent:
            alerts.append(f"🔴 {name} ({addr}) — В МАРШРУТИЗАЦИИ ({', '.join(roles)}), НО ТРАФИКА НЕТ (0 юзеров, <{ng['silent_mbit']:.0f} Мбит), "
                          f"пока флот несёт {fleet_mbit:.0f} Мбит. Похоже на блокировку IP со стороны клиентов. Проверить С МОСТА: "
                          f"timeout 5 bash -c 'cat </dev/null >/dev/tcp/{addr}/2053' && echo OPEN || echo FAIL. "
                          f"Пинг идёт, а TCP закрыт со всех сетей страны — блокировка по IP, лечится вторым IP.")
    elif silent:
        print(f"(молчат {len(silent)}, но флот несёт всего {fleet_mbit:.0f} Мбит — не считаю тревогой)")
    for why in parked_seen:
        table.append(f"🅿️ отставлена намеренно: {why}")

    print("\n".join(table))
    print(f"\nфлот несёт суммарно {fleet_mbit:.0f} Мбит")
    if alerts:
        print("\nНАШЛОСЬ:")
        for x in alerts:
            print(" ", x)
    else:
        print("всё в норме")
    body = ("<b>Ноды: есть проблемы</b>\n" + "\n".join(esc(x) for x in alerts) + "\n\n" if alerts else "<b>Ноды: всё в норме</b>\n") \
        + "<pre>" + "\n".join(esc(x) for x in table) + "</pre>"
    if alerts or a.force:
        telegram_send(cfg["telegram"], body, a.quiet)
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
