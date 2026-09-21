# Справочник: команды, флаги, ключи конфига, файлы, коды выхода

Собран из исходников (`add_argument`) — то же, что печатает `--help`.

## node-guard

```
node-guard [--config ПУТЬ] [--force] [--quiet]
```

| флаг | что делает |
|---|---|
| `--config` | путь к конфигу (по умолчанию /etc/fleet-guards/config.json или FLEET_GUARDS_CONFIG) |
| `--force` | прислать отчёт, даже если всё в норме |
| `--quiet` | только в консоль, без Telegram |

Коды выхода: 0 — всё в норме, 1 — нашлись проблемы (письмо ушло), 2 — ошибка конфига/панели.

## traffic-guard

```
traffic-guard [--config ПУТЬ] [--report] [--force] [--quiet]
```

| флаг | что делает |
|---|---|
| `--config` | путь к конфигу (по умолчанию /etc/fleet-guards/config.json или FLEET_GUARDS_CONFIG) |
| `--force` | прислать отчёт, даже если всё в норме |
| `--quiet` | только в консоль, без Telegram |
| `--report` | напечатать таблицу по всем нодам |

Код выхода 0. Состояние — `/var/lib/fleet-guards/traffic.json`: по каждой ноде период, накопленные rx/tx, последние значения счётчиков, `started_at`, отправленные пороги.

## fleet-guards

```
fleet-guards [--config ПУТЬ] setup [флаги]
fleet-guards [--config ПУТЬ] doctor [--no-telegram]
```

### `fleet-guards setup`

| флаг | что делает |
|---|---|
| `--panel-url` | адрес панели |
| `--token` | API-токен панели |
| `--bridge-profiles` | профили мостов: номера или имена через запятую; пустая строка — без проверки «молчит» |
| `--report-hour` | час ежедневного отчёта (0–23) |
| `--included-tb` | объём трафика по умолчанию, ТБ (0 — только ноды с лимитом в панели) |
| `--billing` | как хостер считает трафик |
| `--tg-token` | токен Telegram-бота (пустая строка — без Telegram) |
| `--tg-chat` | chat_id получателя; без него мастер определит сам |
| `--tg-proxy` | HTTP-прокси для api.telegram.org |
| `--skip-telegram-test` | не слать тестовое сообщение |
| `--no-enable` | не включать таймеры |
| `--yes, -y` | принимать значения по умолчанию без вопросов |

### `fleet-guards doctor`

| флаг | что делает |
|---|---|
| `--no-telegram` | не слать тестовое сообщение |

Коды выхода `setup`: 0 — готово, 2 — панель не ответила; `doctor`: 0 — всё ✅, 1 — есть ❌. Ctrl+C — 130.

## Ключи конфига `/etc/fleet-guards/config.json`

| ключ | что | по умолчанию |
|---|---|---|
| `panel.url`, `panel.token` | панель и API-токен (чтение) | — |
| `telegram.bot_token`, `chat_id`, `proxy` | бот, получатель, HTTP-прокси для api.telegram.org | — |
| `node_guard.report_hour` | час ежедневного отчёта (в таймер пишет мастер) | 21 |
| `node_guard.load_per_core` | тревога, если load/ядро ≥ | 0.8 |
| `node_guard.mem_free_min` | тревога, если свободной памяти < (доля) | 0.12 |
| `node_guard.bw_share` | тревога, если полоса ≥ доля от `capacity_mbit` | 0.7 |
| `node_guard.silent_mbit` | «молчит» = меньше этого при 0 юзеров | 1.0 |
| `node_guard.fleet_busy_mbit` | «молчит» считается тревогой, только если флот несёт ≥ | 30 |
| `node_guard.bridge_profiles` | имена профилей мостов, из чьих балансиров и правил берутся «обязанные» ноды | [] |
| `node_guard.capacity_mbit` | ёмкость канала по нодам, Мбит (имя → число) | {} |
| `node_guard.alt_ips` | второй адрес → адрес ноды в панели | {} |
| `node_guard.parked` | адрес или имя ноды → причина, почему молчит сознательно | {} |
| `node_guard.chains` | список `{name, entry, exit, min_entry_users, min_exit_mbit}` | [] |
| `traffic_guard.included_tb_default` | объём для нод без лимита в панели и без override, ТБ; 0 — не считать | 0 |
| `traffic_guard.overrides` | имя ноды → ТБ (важнее лимита в панели) | {} |
| `traffic_guard.marks` | пороги, % | [70, 85, 95] |
| `traffic_guard.billing` | `max` — большее из направлений, `sum` — вход + выход | max |
| `paths.state_dir` | где состояние | /var/lib/fleet-guards |

## Файлы и переменные

`/opt/fleet-guards/` — скрипты; `/etc/fleet-guards/config.json` (600); `/var/lib/fleet-guards/traffic.json`; юниты `node-guard.service/.timer` (`OnCalendar` час из конфига, `SuccessExitStatus=0 1`), `traffic-guard.service/.timer` (`OnUnitActiveSec=30min`). Переменные: `FLEET_GUARDS_CONFIG` — путь к конфигу; для установщика `NO_SETUP=1`, `FLEET_GUARDS_SRC_URL`.
