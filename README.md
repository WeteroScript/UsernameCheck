# UsernameCheck

Telegram-бот на Python 3.10+ и aiogram 3.x.

## Переменные окружения

Все настройки читаются из переменных окружения. На хостинге создай их в панели проекта
(обычно раздел называется **Environment Variables**, **Variables** или **Secrets**).
Файл `.env` на хостинге создавать не требуется.

| Переменная | Обязательна | Назначение |
|---|---|---|
| `BOT_TOKEN` | Да | Токен Telegram-бота |
| `API_ID` | Да | Telegram API ID, только число |
| `API_HASH` | Да | Telegram API Hash |
| `TOKENPLAN_API_KEY` | Для чата с ИИ | Ключ TokenPlan |
| `TOKENPLAN_BASE_URL` | Нет | URL Chat Completions API; по умолчанию используется TokenPlan |
| `AI_CHAT_HISTORY_FILE` | Нет | Путь к файлу истории; по умолчанию `data/ai_chats.json` |
| `USE_LOCAL_BOT_API` | Нет | Использовать локальный Bot API; в Docker включён по умолчанию |
| `TELEGRAM_BOT_API_URL` | Нет | Адрес локального Bot API; по умолчанию `http://127.0.0.1:8081` |
| `AI_CHAT_MAX_UPLOAD_MB` | Нет | Лимит файла для ИИ-чата в МБ; по умолчанию `200` |

Значения `BOT_TOKEN`, `API_HASH` и `TOKENPLAN_API_KEY` храни только в настройках хостинга,
не добавляй их в GitHub. Для `AI_CHAT_HISTORY_FILE` используй путь внутри постоянного
диска/volume хостинга: файловая система контейнера может очищаться при redeploy.

## Запуск без Docker

1. Установи Python 3.10 или новее.
2. Установи зависимости:

   ```bash
   pip install -r requirements.txt
   ```

3. Скопируй `.env.example` в локальный `.env` и заполни `BOT_TOKEN`, `API_ID` и `API_HASH`.
4. Для раздела «Чат с ИИ» укажи `TOKENPLAN_API_KEY`. При необходимости поменяй `TOKENPLAN_BASE_URL`.
5. Запусти:

   ```bash
   python Bot.py
   ```

## Чат с ИИ

В главном меню открой «Чат с ИИ» → «Новый чат» → «Выбрать модель».
Поддерживаются модели, перечисленные в `ai_chat.py`. Каждый диалог хранится отдельно
в JSON-файле, привязанном к Telegram user_id. Путь задаётся переменной
`AI_CHAT_HISTORY_FILE` (по умолчанию `data/ai_chats.json`).

API-ключ читается из переменной окружения `TOKENPLAN_API_KEY` и не должен попадать
в исходный код или Git. Не загружай локальный `.env` в репозиторий.

## Local Telegram Bot API и большие файлы

Docker-образ собирает официальный Local Telegram Bot API Server и запускает его
внутри контейнера на `127.0.0.1:8081`. Python-бот подключается к нему через aiogram.
Этот порт не нужно публиковать наружу. HTTP-порт `3000` используется только для
health endpoint хостинга.

Local Bot API позволяет скачивать файлы без стандартного ограничения размера
загрузки и загружать файлы размером до 2000 МБ. В ИИ-чате отдельно действует
лимит `AI_CHAT_MAX_UPLOAD_MB` (по умолчанию 200 МБ). Фактическая обработка также
ограничивается доступной памятью контейнера и лимитами хостинга.

**Однократное переключение уже запущенного бота:** перед первым запуском через
Local Bot API нужно вызвать метод `logOut` на стандартном сервере Telegram:
`https://api.telegram.org/bot<TOKEN>/logOut`. Это действие выполняется ровно
один раз для перехода с облачного Bot API на локальный. Не вызывай его при каждом
перезапуске или redeploy. После переключения бот должен работать через локальный
адрес. Подробности: https://core.telegram.org/bots/api#using-a-local-bot-api-server

В Bothost добавь те же `BOT_TOKEN`, `API_ID`, `API_HASH` и остальные секреты,
которые использовались ранее. Для сохранения состояния Telegram Bot API и истории
ИИ-чатов подключи постоянный volume к `/app/data`. Убедись, что тариф Bothost
разрешает сборку образа с C++ и запуск нескольких процессов в одном контейнере.

## Docker

Сборка образа:

```bash
docker build -t username-check .
```

Запуск с локальным файлом окружения и постоянным томом:

```bash
docker run --env-file .env -p 3000:3000 -v username-check-data:/app/data username-check
```
