# Runbook: деплой executor на VPS

Практическое руководство «с нуля до работающего контейнера в live mode».  
Дополняет [deployment_hybrid.md](deployment_hybrid.md) и [SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md](SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md).

**Последнее обновление:** июль 2026 (Contabo VPS, Ubuntu 24.04).

---

## 1. Что поднимается на сервере

| Компонент | Контейнер | Назначение |
|-----------|-----------|------------|
| `executor` | `okx-hft-executor` | торговый цикл (strategy manager → OKX REST) |
| `control-api` | `okx-hft-control-api` | HTTP API на порту **8080** (health, enable/disable стратегий) |

Конфигурация: файл **`.env`** в каталоге проекта (не в git).  
Данные: SQLite в **`./data`** → в контейнере `/app/data/baseline_mvp.sqlite3`.

Типовые пути на сервере (проверьте `pwd` после `git clone`):

```text
/opt/okx-hft-executor/okx-hft-executor/   # корень репозитория
/opt/okx-hft-executor/okx-hft-executor/.env
/opt/okx-hft-executor/okx-hft-executor/data/
```

---

## 2. Предпосылки на VPS

- Ubuntu 22.04 / 24.04, Docker, исходящий HTTPS до `https://www.okx.com`.
- Пользователь **`okx-hft-executor`** (не root), SSH **только по ключу**.
- Security baseline: [SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md](SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md).

### Установка Docker (один раз)

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git curl ufw fail2ban

sudo systemctl enable --now docker
sudo usermod -aG docker okx-hft-executor
```

Перелогиньтесь по SSH, затем:

```bash
docker --version
docker compose version
```

На Ubuntu сервис SSH называется **`ssh`**, не `sshd`:

```bash
sudo systemctl restart ssh
```

---

## 3. Клонирование и `.env`

```bash
sudo mkdir -p /opt/okx-hft-executor
sudo chown okx-hft-executor:okx-hft-executor /opt/okx-hft-executor
cd /opt/okx-hft-executor
git clone <URL_РЕПОЗИТОРИЯ> okx-hft-executor
cd okx-hft-executor
```

`.env` **не коммитится**. Создайте на сервере или скопируйте с рабочей машины.

### Загрузка `.env` с Windows (PowerShell)

```powershell
scp -i C:\Users\<USER>\.ssh\id_ed25519 D:\tumar\okx-hft-executor\.env okx-hft-executor@<VPS_IP>:/opt/okx-hft-executor/okx-hft-executor/.env
```

На сервере:

```bash
chmod 600 .env
ls -la .env    # файл скрытый — обычный ls его не покажет
```

Шаблон переменных: [.env.example](../.env.example). Подробнее про режимы: [getting_started.md](getting_started.md).

---

## 4. Права на каталог `data/` (обязательно)

Контейнер работает от пользователя **`app`** (в образе `python:3.11-slim`).  
Volume `./data:/app/data` монтируется с хоста; если каталог создан от root, SQLite падает с:

```text
sqlite3.OperationalError: unable to open database file
```

**Перед первым запуском:**

```bash
cd /opt/okx-hft-executor/okx-hft-executor
mkdir -p data

# узнать uid/gid пользователя app в образе
sudo docker compose run --rm --entrypoint id executor
# типично: uid=100(app) gid=101(app)

sudo chown -R 100:101 data
sudo chmod 755 data
ls -la data
```

Проверка записи из контейнера:

```bash
sudo docker compose exec executor sh -c "touch /app/data/.test && rm /app/data/.test && echo OK"
```

> UID может отличаться после смены базового образа — всегда перепроверяйте через `id executor`.

---

## 5. Режимы: paper vs live

Реальный OKX REST (`OkxRestClient`) включается в `app/bootstrap.py`, если **нет** заглушки:

| Условие | Результат |
|---------|-----------|
| `OKX_HFT_RUNTIME_MODE=replay` | заглушка |
| `OKX_HFT_SAFE_MODE=1` | заглушка |
| `paper` + `OKX_ENABLE_REAL_OKX_IN_PAPER=0` | заглушка |
| иначе + валидные `OKX_API_*` | **реальные заявки** |

### Безопасный старт (без заявок на биржу)

```env
OKX_HFT_RUNTIME_MODE=paper
OKX_HFT_SAFE_MODE=1
```

### Live на OKX demo (реальные заявки на demo-счёт)

```env
OKX_HFT_RUNTIME_MODE=live
OKX_HFT_SAFE_MODE=0
OKX_FLAG_DEMO=1
OKX_API_KEY=...
OKX_API_SECRET=...
OKX_API_PASSPHRASE=...
OKX_BASE_URL=https://www.okx.com
OKX_HFT_CONTROL_API_TOKEN=<длинный-случайный-токен>
```

В `docker-compose.yml` для контейнеров задано `OKX_SQLITE_PATH=/app/data/baseline_mvp.sqlite3` — это переопределяет значение из `.env`.

---

## 6. Команды Docker (ежедневные)

Рабочий каталог: корень репозитория с `docker-compose.yml`.

```bash
cd /opt/okx-hft-executor/okx-hft-executor

# сборка и запуск в фоне
sudo docker compose up -d --build

# статус
sudo docker compose ps

# логи (executor = торговля)
sudo docker compose logs -f executor
sudo docker compose logs -f control-api

# остановка
sudo docker compose down

# перезапуск после смены .env (без пересборки)
sudo docker compose up -d

# dry-run конфига внутри контейнера
sudo docker compose exec executor python -m app.main --dry-run
```

После `usermod -aG docker` можно убрать `sudo` (нужен новый SSH-вход).

---

## 7. Проверки после старта

**На сервере:**

```bash
curl -s http://127.0.0.1:8080/health/liveness
```

**С рабочей машины** (если UFW разрешил ваш IP на 8080):

```bash
curl http://<VPS_IP>:8080/health/liveness
curl -H "X-API-Key: <token>" http://<VPS_IP>:8080/strategies
```

В логах `executor` при live ожидается:

```text
режим=live
exchange.okx.rest_client
runtime_mode=live safe_mode=False
postgres journal enabled schema=okx_exec
postgres executor_run started run_id=...
strategy manager started
```

Оба контейнера в `docker compose ps`: **Up (healthy)**.

### PostgreSQL (после обновления кода с measurement)

```bash
sudo docker compose exec executor python scripts/apply_pg_migrations.py
```

Миграции `005` (колонки `trade_results`) и `006` (view daily summary). Подробнее: [baseline_measurement.md](baseline_measurement.md).

### Дневная аналитика

```bash
sudo docker compose exec executor python scripts/trade_daily_summary.py --strategy random_baseline_v1
```

---

## 8. Firewall (UFW)

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow from <YOUR_HOME_IP> to any port 8080 proto tcp comment 'control-api'
sudo ufw enable
sudo ufw status numbered
```

Порт **8080** не открывать для `0.0.0.0/0`.

---

## 9. Обновление версии

Вручную:

```bash
cd /opt/okx-hft-executor/okx-hft-executor
git pull
sudo docker compose up -d --build
sudo docker compose exec executor python scripts/apply_pg_migrations.py
```

**Автодеплой при push в `main`:** [deployment_cicd.md](deployment_cicd.md).

Если меняли только `.env`:

```bash
sudo docker compose up -d
```

---

## 10. Частые проблемы

| Симптом | Решение |
|---------|---------|
| `docker: command not found` | `sudo apt install -y docker.io docker-compose-v2` |
| `unable to open database file` | `mkdir -p data && sudo chown -R 100:101 data` (см. §4) |
| `.env` не виден в `ls` | `ls -la .env` (скрытый файл) |
| `control-api` падает с `unrecognized arguments: uvicorn` | в compose для `control-api` задан отдельный `entrypoint` на uvicorn (см. `docker-compose.yml`) |
| В логах `stub_client` при live | `OKX_HFT_SAFE_MODE=1` или нет ключей OKX |
| `sshd.service not found` | на Ubuntu: `sudo systemctl restart **ssh**` |
| `Permission denied` при SSH по ключу | проверить `~/.ssh/authorized_keys`, права `700`/`600`, владелец каталога |

---

## 11. Связанные документы

| Документ | Содержание |
|----------|------------|
| [getting_started.md](getting_started.md) | локальный запуск, переменные `.env` |
| [deployment_hybrid.md](deployment_hybrid.md) | hybrid-модель, control-api, стратегии |
| [SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md](SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md) | SSH, UFW, Fail2Ban |
| [control_api.md](control_api.md) | HTTP API |
| [baseline_measurement.md](baseline_measurement.md) | net PnL, fees, daily summary |

---

## 12. Быстрая шпаргалка (уже настроенный сервер)

```bash
ssh okx-hft-executor@<VPS_IP>
cd /opt/okx-hft-executor/okx-hft-executor
sudo docker compose ps
sudo docker compose logs --tail 50 executor
sudo docker compose up -d          # поднять
sudo docker compose down           # остановить
```
