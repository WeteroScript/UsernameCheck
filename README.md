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

Значения `BOT_TOKEN`, `API_HASH` и `TOKENPLAN_API_KEY` храни только в настройках хостинга,
не добавляй их в GitHub. Для `AI_CHAT_HISTORY_FILE` используй путь внутри постоянного
диска/volume хостинга: файловая система контейнера может очищаться при redeploy.

## Запуск

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

## Docker

Сборка образа:

```bash
docker build -t username-check .
```

Запуск с локальным файлом окружения и отдельным томом для истории:

```bash
docker run --env-file .env -v username-check-data:/app/data username-check
```

