# UsernameCheck

Telegram-бот на Python 3.10+ и aiogram 3.x.

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

