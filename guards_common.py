#!/usr/bin/env python3
"""Общее для сторожей: конфиг, панель Remnawave, Telegram. Только чтение панели."""
import argparse
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
DEFAULT_CONFIG = os.environ.get("FLEET_GUARDS_CONFIG", "/etc/fleet-guards/config.json")
UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def is_ip(s):
    try:
        ipaddress.ip_address(str(s))
        return True
    except ValueError:
        return False


def read_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("panel", {})
    cfg.setdefault("telegram", {})
    cfg.setdefault("node_guard", {})
    cfg.setdefault("traffic_guard", {})
    cfg.setdefault("paths", {})
    cfg["paths"].setdefault("state_dir", "/var/lib/fleet-guards")
    cfg["paths"].setdefault("xray", "/opt/fleet-guards/xray")
    return cfg


def load_config(path):
    try:
        cfg = read_config(path)
    except FileNotFoundError:
        print(f"нет конфига {path} — запустите: fleet-guards setup", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"конфиг {path} — не JSON: {e}", file=sys.stderr)
        sys.exit(2)
    if not (cfg["panel"].get("url") and cfg["panel"].get("token")):
        print("в конфиге нет panel.url / panel.token — запустите: fleet-guards setup", file=sys.stderr)
        sys.exit(2)
    return cfg


def write_config(path, cfg):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.exists(path):
        bak = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        os.replace(path, bak)
        print(f"  старый конфиг сохранён: {bak}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_state(cfg, name):
    p = os.path.join(cfg["paths"]["state_dir"], name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(cfg, name, state):
    d = cfg["paths"]["state_dir"]
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, name))


class Panel:
    """Тонкий клиент Remnawave API. Сторожа только читают."""

    def __init__(self, url, token):
        self.url, self.token = url.rstrip("/"), token

    def get(self, path):
        req = urllib.request.Request(self.url + path, headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = json.loads(r.read().decode())
        return payload.get("response", payload)

    @staticmethod
    def _list(data, key):
        return data.get(key, data) if isinstance(data, dict) else data

    def nodes(self):
        return self._list(self.get("/api/nodes"), "nodes")

    def profiles(self):
        return self._list(self.get("/api/config-profiles"), "configProfiles")

    def profile(self, name_or_uuid):
        return next((p for p in self.profiles() if name_or_uuid in (p.get("uuid"), p.get("name"))), None)

    def hosts(self):
        return self._list(self.get("/api/hosts"), "hosts")


def node_metrics(n):
    """Метрики ноды из ответа панели: ядра, load, память, полоса сейчас, сырые счётчики."""
    sysd = n.get("system") or {}
    info, stats = sysd.get("info") or {}, sysd.get("stats") or {}
    iface = stats.get("interface") or {}
    cpus = info.get("cpus") or 1
    la = (stats.get("loadAvg") or [0])[0]
    mt, mf = info.get("memoryTotal") or 0, stats.get("memoryFree") or 0
    return {
        "cpus": cpus, "load": la, "per_core": la / cpus,
        "mem_free_share": (mf / mt) if mt else 1.0,
        "mbit": max(iface.get("rxBytesPerSec") or 0, iface.get("txBytesPerSec") or 0) * 8 / 1e6,
        "rx_total": int(float(iface.get("rxTotal") or 0)), "tx_total": int(float(iface.get("txTotal") or 0)),
        "users": n.get("usersOnline") or 0,
    }


def telegram_api(tg, method, params=None, timeout=25):
    data = urllib.parse.urlencode(params or {}).encode() if params else None
    proxy = tg.get("proxy") or None
    last = None
    for p in ([proxy, None] if proxy else [None, None]):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": p} if p else {}))
            raw = opener.open(urllib.request.Request(f"https://api.telegram.org/bot{tg['bot_token']}/{method}", data=data),
                              timeout=timeout).read()
            return json.loads(raw.decode()), None
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode())
            except Exception:  # noqa: BLE001
                body = {}
            return None, f"{e.code} {body.get('description') or e.reason}"
        except Exception as e:  # noqa: BLE001
            last = e
    return None, str(last)[:100]


def telegram_send(tg, text, quiet=False):
    """Сообщение админу (HTML). Через прокси → напрямую, несколько попыток: с российских
    серверов api.telegram.org доставляется нестабильно, а одна попытка = потерянная тревога."""
    if quiet or not tg.get("bot_token") or not tg.get("chat_id"):
        print("  telegram: тихо (quiet или не настроен)")
        return False
    data = urllib.parse.urlencode({"chat_id": tg["chat_id"], "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    proxy = tg.get("proxy") or None
    plan = [proxy, None, proxy, None, None, None] if proxy else [None] * 6
    last = None
    for i, p in enumerate(plan):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": p} if p else {}))
            opener.open(urllib.request.Request(f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage", data=data),
                        timeout=20).read()
            print("  telegram: отправлено" + (f" (с {i + 1}-й попытки)" if i else ""))
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(2 + i, 8))
    print("  telegram НЕ отправился:", str(last)[:100])
    return False


def telegram_detect_chat(tg, wait_s=90):
    me, err = telegram_api(tg, "getMe")
    if not me:
        return None, f"токен не принят Telegram ({err}) — проверьте, что скопировали его целиком у @BotFather"
    username = me["result"].get("username")
    print(f"  бот найден: @{username}. Откройте https://t.me/{username} и нажмите Start (или напишите ему что угодно).")
    print(f"  жду сообщение до {wait_s} с…")
    t0, offset = time.time(), None
    while time.time() - t0 < wait_s:
        params = {"timeout": 15, "allowed_updates": json.dumps(["message"])}
        if offset:
            params["offset"] = offset
        upd, err = telegram_api(tg, "getUpdates", params, timeout=30)
        if not upd:
            if err and err.startswith("409"):
                return None, ("этот бот уже используется другой программой (getUpdates занят или включён webhook). "
                              "Для тревог заведите ОТДЕЛЬНОГО бота у @BotFather или введите chat_id руками")
            time.sleep(2)
            continue
        for u in upd.get("result", []):
            offset = u["update_id"] + 1
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("id"):
                who = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or "?"
                return chat["id"], who
    return None, "за отведённое время сообщение не пришло"


def common_args(ap):
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="путь к конфигу (по умолчанию /etc/fleet-guards/config.json или FLEET_GUARDS_CONFIG)")
    ap.add_argument("--force", action="store_true", help="прислать отчёт, даже если всё в норме")
    ap.add_argument("--quiet", action="store_true", help="только в консоль, без Telegram")
    ap.add_argument("--version", action="version", version=VERSION)
    return ap
