# CI/CD: тесты, образ в GHCR и деплой на VPS

Цепочка при push в `main`:

```text
CI (pytest, ruff, mypy, docker smoke) → сборка образа → ghcr.io → ручное подтверждение → SSH на VPS → docker compose pull + up
```

Workflow:

- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — CI на каждый PR и push в `develop`; для `main` вызывается из деплоя.
- [.github/workflows/deploy-vps.yml](../.github/workflows/deploy-vps.yml) — сборка образа и деплой.

Образ: `ghcr.io/sorokad/tumar-executor`, теги `<полный sha коммита>` и `latest`.
На сервере код больше не собирается: из git берётся только `docker-compose.yml`, сам код приходит в образе.

---

## 1. Когда срабатывает

| Событие | Что запускается |
|---------|-----------------|
| Pull request в любую ветку | CI |
| `push` в `develop` | CI |
| `push` в `main` с изменением кода сервиса (см. `paths` в `deploy-vps.yml`) | CI → образ → деплой (после подтверждения) |
| `workflow_dispatch` | ручной запуск Deploy VPS из GitHub → Actions |

Правки только в `docs/**`, `*.md`, `tests/**` деплой не запускают.

### Что блокирует деплой

- `pytest` — блокирует.
- Сборка Docker-образа и `--dry-run` внутри него (replay, safe mode, без Postgres) — блокирует.
- `ruff` и `mypy --strict` — пока **не** блокируют (`continue-on-error`), результат виден в логе job.
  Когда код будет вычищен, уберите `continue-on-error` в `ci.yml`.

---

## 2. Однократная настройка GitHub

### Секреты

Репозиторий → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret | Пример значения |
|--------|-----------------|
| `VPS_SSH_HOST` | IP или hostname VPS |
| `VPS_SSH_USER` | `okx-hft-executor` |
| `VPS_SSH_PRIVATE_KEY` | содержимое `okx-hft-deploy` (приватный ключ, целиком) |
| `VPS_DEPLOY_PATH` | `/opt/okx-hft-executor/okx-hft-executor` |

Токен для GHCR заводить не нужно: используется `GITHUB_TOKEN` текущего запуска.
На сервере он нужен только на время `docker compose pull`, после деплоя выполняется `docker logout`.

### Окружение `production` (ручное подтверждение)

**Settings** → **Environments** → **New environment** → `production`:

- **Required reviewers** — добавьте себя. Без этого GitHub создаст окружение автоматически, но без защиты, и деплой пойдёт сразу.
- **Deployment branches** → *Selected branches* → `main`.

### Защита ветки `main`

**Settings** → **Branches** → **Add rule** для `main`:

- Require a pull request before merging.
- Require status checks to pass: `test`, `docker` (из workflow CI).

---

## 3. Однократная настройка VPS

Сервер уже с Docker и клоном репозитория (см. [deployment_vps_runbook.md](deployment_vps_runbook.md)).

```bash
cd /opt/okx-hft-executor/okx-hft-executor
git remote -v   # origin → ваш GitHub
test -f .env && chmod 600 .env
docker compose version   # нужна v2.17+ для `up --wait --wait-timeout`
```

`.env` **остаётся только на сервере**, в git не коммитить.

Пользователь деплоя должен уметь запускать docker без sudo (`usermod -aG docker <user>`)
или через `sudo` без пароля — workflow сам выберет вариант.

### Deploy-ключ для GitHub Actions

На **вашем ПК** (отдельный ключ, не личный):

```powershell
ssh-keygen -t ed25519 -C "github-actions-deploy-okx-hft" -f $env:USERPROFILE\.ssh\okx-hft-deploy -N '""'
```

Публичный ключ на **VPS**:

```powershell
type $env:USERPROFILE\.ssh\okx-hft-deploy.pub | ssh okx-hft-executor@<VPS_IP> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

---

## 4. Что делает деплой на сервере

1. `git fetch` + `git reset --hard <sha>` — только ради `docker-compose.yml` нужной версии.
2. Проверка `.env`.
3. `docker login ghcr.io` временным `GITHUB_TOKEN`.
4. `EXECUTOR_IMAGE_TAG=<sha> docker compose pull`.
5. `docker compose up -d --no-build --remove-orphans --wait` — ждёт, пока healthcheck станет healthy.
6. Smoke: `python -m app.main --dry-run` в контейнере executor.
7. `docker logout ghcr.io`.

Локально `docker compose up --build` по-прежнему собирает образ из исходников.

---

## 5. Откат

Вариант 1 — из GitHub: Actions → Deploy VPS → нужный старый успешный запуск → **Re-run all jobs**.
Будет задеплоен образ с sha того запуска.

Вариант 2 — вручную на VPS:

```bash
cd /opt/okx-hft-executor/okx-hft-executor
git fetch origin && git reset --hard <старый_sha>
EXECUTOR_IMAGE_TAG=<старый_sha> docker compose pull
EXECUTOR_IMAGE_TAG=<старый_sha> docker compose up -d --no-build
```

Список доступных тегов: GitHub → профиль → **Packages** → `tumar-executor`.

---

## 6. Workflow упал

GitHub → run → нужный job → лог шага.

| Сообщение | Решение |
|-----------|---------|
| падает job `test` | тесты не проходят — чинить код, деплой не начнётся |
| падает job `docker` | образ не собирается или `--dry-run` в нём падает |
| `VPS_DEPLOY_PATH is empty` | секрет `VPS_DEPLOY_PATH` в GitHub Actions |
| `not a git repo` | на VPS: `git clone` в `DEPLOY_PATH` |
| `Permission denied (publickey)` | deploy-ключ в `authorized_keys` + `VPS_SSH_PRIVATE_KEY` |
| `.env missing` | `scp` `.env` на сервер |
| `unknown flag: --wait-timeout` | обновить Docker Compose на VPS до v2.17+ |
| `denied` при `docker compose pull` | у workflow должно быть `packages: read`; пакет в GHCR должен быть привязан к репозиторию |
| `dry-run` failed | `docker compose logs executor` на VPS |

Проверка ключа с ПК:

```powershell
ssh -i C:\Users\sorok\.ssh\okx-hft-deploy okx-hft-executor@<VPS_IP> "cd /opt/okx-hft-executor/okx-hft-executor && docker compose ps"
```

---

## 7. Безопасность

- Отдельный deploy-ключ, не ваш личный.
- SSH на VPS только по ключу ([SECURITY_BASELINE](SECURITY_BASELINE_VPS_SSH_AND_NETWORK.md)).
- Секреты OKX только в `.env` на сервере.
- Деплой торгового кода — только после ручного подтверждения в окружении `production`.
- `concurrency` в workflow — не два деплоя одновременно на один хост.
