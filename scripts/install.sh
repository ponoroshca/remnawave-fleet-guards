#!/usr/bin/env bash
# install.sh — установка fleet-guards (Debian/Ubuntu, root). Сторожа только читают панель и пишут в Telegram.
#   из клона:      sudo ./scripts/install.sh
#   одной строкой: curl -fsSL https://raw.githubusercontent.com/ponoroshca/remnawave-fleet-guards/main/scripts/install.sh | sudo bash
#   NO_SETUP=1 — только установить, мастер не запускать.
# Пишет только в /opt/fleet-guards, /etc/fleet-guards, /var/lib/fleet-guards, /usr/local/bin/{fleet-guards,node-guard,traffic-guard}
# и юниты node-guard.*, traffic-guard.*. Чужие файлы с такими именами не затирает — останавливается.
set -euo pipefail
OPT=/opt/fleet-guards; ETC=/etc/fleet-guards; VAR=/var/lib/fleet-guards
SRC_URL="${FLEET_GUARDS_SRC_URL:-https://github.com/ponoroshca/remnawave-fleet-guards/archive/refs/heads/main.tar.gz}"
[ "$(id -u)" = 0 ] || { echo "нужен root (sudo)"; exit 1; }
command -v python3 >/dev/null || { echo "нужен python3"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "нужен Python 3.9+"; exit 1; }
for t in curl tar; do command -v "$t" >/dev/null || { apt-get update -qq && apt-get install -y -qq "$t"; }; done
for u in node-guard.service node-guard.timer traffic-guard.service traffic-guard.timer; do
  f="/etc/systemd/system/$u"
  if [ -f "$f" ] && ! grep -q "/opt/fleet-guards/\|node-guard —\|traffic-guard —" "$f"; then
    echo "СТОП: $f уже существует и это не наш юнит. Переименуйте его. Ничего не изменено."; exit 1
  fi
done
for b in fleet-guards node-guard traffic-guard; do
  f="/usr/local/bin/$b"
  if [ -e "$f" ] && { [ ! -L "$f" ] || [[ "$(readlink -f "$f")" != /opt/fleet-guards/* ]]; }; then
    echo "СТОП: $f уже существует и это не наша ссылка. Ничего не изменено."; exit 1
  fi
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)"; WORK=""
if [ -n "$HERE" ] && [ -f "$HERE/node_guard.py" ]; then SRC="$HERE"; else
  WORK=$(mktemp -d); echo "исходники: $SRC_URL"
  curl -fsSL --retry 3 -o "$WORK/src.tar.gz" "$SRC_URL"; tar -xzf "$WORK/src.tar.gz" -C "$WORK"
  SRC=$(dirname "$(find "$WORK" -name node_guard.py | head -1)"); [ -n "$SRC" ] || { echo "в архиве нет node_guard.py"; exit 1; }
fi
trap '[ -n "$WORK" ] && rm -rf "$WORK"' EXIT
mkdir -p "$OPT" "$ETC" "$VAR"; chmod 700 "$ETC" "$VAR"
install -m 755 "$SRC/node_guard.py" "$SRC/traffic_guard.py" "$SRC/fleet_guards.py" "$SRC/scripts/uninstall.sh" "$OPT/"
install -m 644 "$SRC/guards_common.py" "$OPT/"
ln -sf "$OPT/fleet_guards.py" /usr/local/bin/fleet-guards; ln -sf "$OPT/node_guard.py" /usr/local/bin/node-guard; ln -sf "$OPT/traffic_guard.py" /usr/local/bin/traffic-guard
install -m 644 "$SRC"/systemd/*.service "$SRC"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload 2>/dev/null || echo "systemd недоступен (контейнер?) — юниты скопированы"
echo "установлено: $OPT (в PATH: fleet-guards, node-guard, traffic-guard)"
if [ "${NO_SETUP:-0}" != 1 ] && [ -t 1 ] && [ -r /dev/tty ]; then echo; echo "Запускаю мастер (Ctrl+C — прервать; позже: fleet-guards setup)…"; echo; fleet-guards setup </dev/tty || true
else echo "Дальше: fleet-guards setup"; fi
