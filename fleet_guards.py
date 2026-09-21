#!/usr/bin/env python3
"""fleet-guards — мастер настройки и чек-лист для сторожей нод и трафика.

  fleet-guards setup            # спросит панель, Telegram, профили мостов; покажет отчёты; включит таймеры
  fleet-guards doctor           # чек-лист: конфиг, панель, профили, Telegram, таймеры, последние прогоны
"""
import argparse
import getpass
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guards_common import (DEFAULT_CONFIG, VERSION, Panel, is_ip, read_config, telegram_detect_chat,  # noqa: E402
                           telegram_send, write_config)

HERE = os.path.dirname(os.path.abspath(__file__))


def ask(prompt, default=None, secret=False, yes=False):
    if yes and default is not None:
        return default
    label = f"{prompt} [{default}]: " if default not in (None, "") else f"{prompt}: "
    while True:
        val = (getpass.getpass(label) if secret else input(label)).strip()
        if val:
            return val
        if default is not None:
            return default


def ask_yes(prompt, default=True, yes=False):
    if yes:
        return default
    val = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not val else val in ("y", "yes", "д", "да")


def run_guard(name, cfg_path, *flags):
    r = subprocess.run([sys.executable, os.path.join(HERE, name), "--config", cfg_path, *flags], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def cmd_setup(a):
    yes = a.yes
    print(f"fleet-guards {VERSION} — мастер настройки. Enter — принять значение в скобках.\n")
    cfg = {}
    if os.path.exists(a.config):
        try:
            cfg = read_config(a.config)
            print(f"  найден конфиг {a.config} — его значения предложены по умолчанию")
        except Exception:  # noqa: BLE001
            cfg = {}
    for k in ("panel", "telegram", "node_guard", "traffic_guard", "paths"):
        cfg.setdefault(k, {})

    print("── 1/4 Панель Remnawave ──")
    print("  API-токен: в панели → API Tokens → Create → имя fleet-guards. Сторожа только читают панель.")
    url = a.panel_url or ask("адрес панели (https://panel.example.com)", cfg["panel"].get("url"), yes=yes)
    if not url.startswith("http"):
        url = "https://" + url
    token = a.token or ask("API-токен панели", cfg["panel"].get("token"), secret=True, yes=yes)
    panel = Panel(url, token)
    try:
        nodes, profs = panel.nodes(), panel.profiles()
    except Exception as e:  # noqa: BLE001
        print("  ❌ панель не ответила:", str(e)[:120])
        return 2
    print(f"  ✅ панель отвечает: нод {len(nodes)}, профилей {len(profs)}")
    cfg["panel"] = {"url": url.rstrip("/"), "token": token}

    print("\n── 2/4 Профили мостов (для проверки «в маршрутизации, но молчит») ──")
    print("  Мост знает, какие ноды ОБЯЗАНЫ нести трафик: они в его балансире и правилах. Укажите профили мостов;")
    print("  если мостов нет — пропустите, остальные проверки (перегруз, память, отвал) работают без этого.")
    with_ru = []
    for p in profs:
        pn = p.get("nodes") or []
        ru = sum(1 for n in pn if (n.get("countryCode") or "").upper() == "RU")
        with_ru.append((ru, len(pn), p["name"]))
    with_ru.sort(key=lambda x: (-x[0], -x[1]))
    for i, (ru, n, name) in enumerate(with_ru, 1):
        print(f"  {i:2}. {name:<28} нод {n:<3} RU {ru}")
    default = ",".join(str(i) for i, (ru, n, name) in enumerate(with_ru, 1) if ru > 0)
    choice = a.bridge_profiles if a.bridge_profiles is not None else ask("номера профилей мостов через запятую (пусто — без этой проверки)", default, yes=yes)
    picked = []
    for x in str(choice).split(","):
        x = x.strip()
        if x.isdigit() and 1 <= int(x) <= len(with_ru):
            picked.append(with_ru[int(x) - 1][2])
        elif x and any(x == p["name"] for p in profs):
            picked.append(x)
    cfg["node_guard"]["bridge_profiles"] = picked
    cfg["node_guard"].setdefault("capacity_mbit", {})
    cfg["node_guard"].setdefault("parked", {})
    cfg["node_guard"].setdefault("alt_ips", {})
    cfg["node_guard"].setdefault("chains", [])
    hour = a.report_hour or ask("час ежедневного отчёта сторожа нод (0–23)", str(cfg["node_guard"].get("report_hour", 21)), yes=yes)
    cfg["node_guard"]["report_hour"] = int(hour) if str(hour).isdigit() else 21
    print(f"  профили мостов: {', '.join(picked) or 'нет'}; отчёт в {cfg['node_guard']['report_hour']}:00. Ёмкость канала (capacity_mbit),"
          " отставленные ноды (parked), вторые IP (alt_ips) и цепочки (chains) — в конфиге, см. docs/reference.md")

    print("\n── 3/4 Трафик у хостера ──")
    print("  Включённый объём берётся из лимита ноды в панели; для нод без лимита можно задать общее значение.")
    cur = cfg["traffic_guard"].get("included_tb_default", 0)
    inc = a.included_tb if a.included_tb is not None else ask("объём по умолчанию, ТБ в месяц (0 — только ноды с лимитом в панели)", str(cur), yes=yes)
    try:
        cfg["traffic_guard"]["included_tb_default"] = float(inc)
    except ValueError:
        cfg["traffic_guard"]["included_tb_default"] = 0
    billing = a.billing or ask("как хостер считает трафик: max — большее из направлений, sum — вход+выход", cfg["traffic_guard"].get("billing", "max"), yes=yes)
    cfg["traffic_guard"]["billing"] = "sum" if str(billing).lower() == "sum" else "max"
    cfg["traffic_guard"].setdefault("marks", [70, 85, 95])
    cfg["traffic_guard"].setdefault("overrides", {})

    print("\n── 4/4 Telegram ──")
    print("  Нужен ОТДЕЛЬНЫЙ бот: @BotFather → /newbot → скопируйте токен вида 123456789:AAF…")
    tg = cfg["telegram"]
    tg["bot_token"] = a.tg_token if a.tg_token is not None else ask("токен бота (Enter — без Telegram)", tg.get("bot_token", ""), secret=True, yes=yes)
    if tg["bot_token"]:
        tg["proxy"] = a.tg_proxy if a.tg_proxy is not None else (tg.get("proxy") or "")
        chat = a.tg_chat or (tg.get("chat_id") if yes else None)
        if not chat and yes:
            print("  ⚠️ --yes без --tg-chat: получатель неизвестен, Telegram отключаю")
            tg["bot_token"] = ""
        elif not chat:
            if tg.get("chat_id") and ask_yes(f"оставить получателя chat_id {tg['chat_id']}", True, yes):
                chat = tg["chat_id"]
            else:
                chat, who = telegram_detect_chat(tg)
                if chat:
                    print(f"  ✅ получатель: {who} (chat_id {chat})")
                else:
                    print(f"  ❌ {who}")
                    chat = ask("chat_id получателя (узнать: напишите @userinfobot)", tg.get("chat_id"), yes=False)
        try:
            tg["chat_id"] = int(chat) if (chat and tg["bot_token"]) else None
        except (TypeError, ValueError):
            print(f"  ❌ chat_id должен быть числом, получено {chat!r} — Telegram отключаю")
            tg["bot_token"], tg["chat_id"] = "", None
        if tg["bot_token"] and tg["chat_id"] and not a.skip_telegram_test:
            if not telegram_send(tg, "✅ fleet-guards: тестовое сообщение — доставка работает.") and not tg["proxy"] and not yes:
                tg["proxy"] = ask("не доставилось. HTTP-прокси для api.telegram.org (Enter — оставить)", "", yes=yes)
                if tg["proxy"]:
                    telegram_send(tg, "✅ fleet-guards: доставка через прокси работает.")
    cfg["telegram"] = tg
    write_config(a.config, cfg)
    print(f"\n  ✅ конфиг записан: {a.config}")

    print("\n── Первый прогон (в консоль) ──")
    rc, out = run_guard("node_guard.py", a.config, "--force", "--quiet")
    print("\n".join("  " + ln for ln in out.splitlines()[-14:]))
    rc2, out2 = run_guard("traffic_guard.py", a.config, "--report", "--quiet")
    print("\n".join("  " + ln for ln in out2.splitlines()[-14:]))

    if shutil.which("systemctl") and not a.no_enable:
        hour = cfg["node_guard"]["report_hour"]
        try:
            with open("/etc/systemd/system/node-guard.timer", "w") as f:
                f.write(f"[Unit]\nDescription=node-guard — ежедневный отчёт о нодах\n[Timer]\nOnCalendar=*-*-* {hour:02d}:00:00\nRandomizedDelaySec=120\nPersistent=true\n[Install]\nWantedBy=timers.target\n")
        except OSError as e:
            print("  ❌ не могу записать таймер:", e)
        if ask_yes("включить таймеры (сторож нод раз в день, трафик раз в 30 минут)", True, yes):
            for unit in ("node-guard.timer", "traffic-guard.timer"):
                r = subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
                r = subprocess.run(["systemctl", "enable", "--now", unit], capture_output=True, text=True)
                print(f"  {'✅' if r.returncode == 0 else '❌'} {unit}" + ("" if r.returncode == 0 else f": {r.stderr.strip()[:100]}"))
    print("\nГотово. Проверка: fleet-guards doctor · отчёт сейчас: node-guard --force · трафик: traffic-guard --report")
    return 0


def cmd_doctor(a):
    ok_all = True

    def item(ok, text, hint=""):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(("  ✅ " if ok else "  ❌ ") + text + (f"\n       → {hint}" if (not ok and hint) else ""))

    print(f"fleet-guards {VERSION} — doctor")
    try:
        cfg = read_config(a.config)
    except FileNotFoundError:
        item(False, f"конфиг {a.config}", "запустите: fleet-guards setup")
        return 1
    except ValueError as e:
        item(False, f"конфиг {a.config} — не JSON: {e}")
        return 1
    item(bool(cfg["panel"].get("url") and cfg["panel"].get("token")), "panel.url и panel.token заданы", "fleet-guards setup")
    try:
        st = os.stat(a.config)
        item(not (st.st_mode & 0o077), "права на конфиг 600", f"chmod 600 {a.config}")
    except OSError:
        pass
    panel = Panel(cfg["panel"].get("url", ""), cfg["panel"].get("token", ""))
    try:
        nodes = panel.nodes()
        item(True, f"панель отвечает, нод {len(nodes)} (на связи {sum(1 for n in nodes if n.get('isConnected'))})")
        profs = {p["name"] for p in panel.profiles()}
        for pname in cfg["node_guard"].get("bridge_profiles") or []:
            item(pname in profs, f"профиль мостов «{pname}» найден", "проверьте node_guard.bridge_profiles")
        with_limit = [n["name"] for n in nodes if float(n.get("trafficLimitBytes") or 0) > 0]
        ov = cfg["traffic_guard"].get("overrides") or {}
        dflt = cfg["traffic_guard"].get("included_tb_default") or 0
        covered = [n["name"] for n in nodes if n["name"] in ov or n["name"] in with_limit or dflt]
        item(bool(covered), f"трафик считается для нод: {', '.join(covered) or 'ни одной'}",
             "задайте лимит трафика нодам в панели, traffic_guard.overrides или included_tb_default")
    except Exception as e:  # noqa: BLE001
        item(False, "панель не отвечает или токен не принят", str(e)[:120])
    if cfg["telegram"].get("bot_token") and cfg["telegram"].get("chat_id"):
        if not a.no_telegram:
            item(telegram_send(cfg["telegram"], "🩺 fleet-guards doctor: тестовое сообщение."), "Telegram доставляет",
                 "проверьте bot_token/chat_id; на серверах в РФ задайте telegram.proxy")
    else:
        item(False, "Telegram настроен", "без него сторожа пишут только в консоль/журнал")
    if shutil.which("systemctl"):
        for unit in ("node-guard.timer", "traffic-guard.timer"):
            act = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
            item(act == "active", f"{unit}: {act or 'не установлен'}", f"systemctl enable --now {unit}")
        for svc in ("node-guard.service", "traffic-guard.service"):
            last = subprocess.run(["systemctl", "show", "-p", "ExecMainExitTimestamp", svc], capture_output=True, text=True).stdout.strip().split("=", 1)[-1]
            if last:
                print(f"       последний прогон {svc}: {last}")
    sp = os.path.join(cfg["paths"]["state_dir"], "traffic.json")
    item(os.path.exists(sp), f"состояние трафика есть ({sp})", "появится после первого прогона traffic-guard")
    print("\n" + ("  всё в порядке" if ok_all else "  есть проблемы — смотрите строки с ❌"))
    return 0 if ok_all else 1


def main():
    ap = argparse.ArgumentParser(description="fleet-guards — мастер и чек-лист сторожей нод и трафика")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="путь к конфигу (по умолчанию /etc/fleet-guards/config.json)")
    ap.add_argument("--version", action="version", version=VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="мастер настройки")
    s.add_argument("--panel-url", help="адрес панели")
    s.add_argument("--token", help="API-токен панели")
    s.add_argument("--bridge-profiles", help="профили мостов: номера или имена через запятую; пустая строка — без проверки «молчит»")
    s.add_argument("--report-hour", help="час ежедневного отчёта (0–23)")
    s.add_argument("--included-tb", help="объём трафика по умолчанию, ТБ (0 — только ноды с лимитом в панели)")
    s.add_argument("--billing", choices=["max", "sum"], help="как хостер считает трафик")
    s.add_argument("--tg-token", help="токен Telegram-бота (пустая строка — без Telegram)")
    s.add_argument("--tg-chat", help="chat_id получателя; без него мастер определит сам")
    s.add_argument("--tg-proxy", help="HTTP-прокси для api.telegram.org")
    s.add_argument("--skip-telegram-test", action="store_true", help="не слать тестовое сообщение")
    s.add_argument("--no-enable", action="store_true", help="не включать таймеры")
    s.add_argument("--yes", "-y", action="store_true", help="принимать значения по умолчанию без вопросов")
    d = sub.add_parser("doctor", help="чек-лист")
    d.add_argument("--no-telegram", action="store_true", help="не слать тестовое сообщение")
    a = ap.parse_args()
    return cmd_setup(a) if a.cmd == "setup" else cmd_doctor(a)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nпрервано")
        sys.exit(130)
