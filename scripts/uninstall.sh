#!/usr/bin/env bash
# uninstall.sh — снять fleet-guards. Конфиг и состояние спрашивает отдельно.
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "нужен root (sudo)"; exit 1; }
systemctl disable --now node-guard.timer traffic-guard.timer 2>/dev/null
rm -f /etc/systemd/system/node-guard.{service,timer} /etc/systemd/system/traffic-guard.{service,timer}; systemctl daemon-reload 2>/dev/null
rm -f /usr/local/bin/fleet-guards /usr/local/bin/node-guard /usr/local/bin/traffic-guard; rm -rf /opt/fleet-guards
echo "таймеры, юниты и /opt/fleet-guards удалены"
a=""; read -r -p "удалить конфиг с токенами (/etc/fleet-guards)? [y/N] " a; [ "${a,,}" = y ] && rm -rf /etc/fleet-guards && echo "  удалён"
a=""; read -r -p "удалить состояние трафика (/var/lib/fleet-guards)? [y/N] " a; [ "${a,,}" = y ] && rm -rf /var/lib/fleet-guards && echo "  удалено"
echo "В панели и на нодах сторожа ничего не меняли — удалять нечего."
