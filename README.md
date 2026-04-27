# Weekly Scanner Scheduler

## Структура файлов

```
scheduler/
├── scheduler.py      — основной скрипт
├── config.yml        — список продуктов, версий и репозиториев
├── .env              — токены (создать из .env.example)
└── .env.example      — пример .env
```

## Установка

### 1. Зависимости

```bash
pip install pyyaml
```

### 2. Создать .env

```bash
cp .env.example .env
# отредактировать .env — вставить GITLAB_TOKEN и настройки DT
```

### 3. Настроить config.yml

Отредактировать `config.yml`:
- `scanner_py` — путь к `scanner.py`
- `work_dir` — куда клонировать репозитории
- `results_dir` — куда складывать результаты
- `env_file` — путь к `.env`
- `products` — список продуктов, версий и репозиториев

### 4. Проверить вручную

```bash
python scheduler.py
```

### 5. Добавить в cron

```bash
crontab -e
```

Добавить строку (каждый понедельник в 02:00):

```
0 2 * * 1 /usr/bin/python3 /opt/scheduler/scheduler.py >> /var/log/scanner-weekly.log 2>&1
```

## Структура результатов

После каждого прогона создаётся папка с датой:

```
/opt/results/
└── 2026-04-28/
    ├── GF__1.0/
    │   ├── report.xlsx
    │   ├── licenses.xlsx
    │   ├── origsbom.json
    │   └── ...
    ├── GF__2.0/
    ├── GF__3.0/
    ├── dev__main/
    └── run.log         ← сводный лог прогона
```

## Структура рабочей директории

```
/work/
├── GF/
│   ├── 1.0/
│   │   ├── backend/    ← git clone
│   │   ├── frontend/   ← git clone
│   │   └── libs/       ← git clone
│   ├── 2.0/
│   └── 3.0/
└── dev/
    └── main/
```

При каждом запуске для каждого репозитория выполняется:
```bash
git fetch --prune
git checkout <branch>
git reset --hard origin/<branch>
git submodule update --init --recursive
```

## Логирование

Лог каждого прогона пишется в:
- stdout/stderr → `/var/log/scanner-weekly.log` (через cron)
- сводный лог → `/opt/results/<date>/run.log`
