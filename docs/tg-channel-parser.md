# TG Channel Parser

Парсер вакансий из Telegram-каналов через **persistent Chrome DevTools Protocol**.

## Зачем

Обычные TG-парсеры используют Telegram Client API (Telethon, Pyrogram) и
требуют собственный логин + API hash. Это даёт:
- **rate-limit** — бан после ~50 сообщений в час
- **detection** — аккаунт в shadow-ban
- **сложность обхода** — капчи, 2FA, спам-фильтры

**Этот парсер использует уже авторизованную Web A сессию** в persistent
Chrome profile. Никаких credentials, никаких API hash, никаких банов —
ты парсишь те каналы, на которые уже подписан, через те cookies, которые
Chrome уже имеет.

## Как работает

1. Persistent Chrome уже запущен на порту 9223 (см. skill
   `chrome-cdp-persistent-profile`).
2. Парсер подключается к Web A через CDP.
3. Переходит по каналам через `dispatchEvent` MouseEvent (verified pattern,
   иначе Web A открывает профиль вместо чата).
4. Извлекает тексты сообщений через `[data-message-id]` селектор.
5. Классифицирует по нишевым IN/OUT-ключевым словам.
6. Возвращает JSON с контактами и оценкой.

## Установка

```bash
pip install websocket-client
```

Persistent Chrome должен быть запущен (см. skill `chrome-cdp-persistent-profile`).

## Использование

```bash
# Парсить 1 канал, последние 20 постов
python scripts/tg_channel_parser.py --channels -1001485944670

# Парсить несколько каналов
python scripts/tg_channel_parser.py \
    --channels -1001485944670 -1001933650950 -1001842475665 \
    --last 30 \
    --out vacancies.json

# Только IN-вакансии (явно наша ниша)
python scripts/tg_channel_parser.py \
    --channels -1001485944670 \
    --filter in \
    --min-score 0.3
```

## Нишевой фильтр

### IN (наши ниши)
- Автоворонки, лид-магниты, email-цепочки
- SMM / контент-план / копирайтинг
- AI / Claude / GPT / нейросети
- Telegram-боты, AI-агенты, автоматизация
- WB / Ozon / маркетплейсы (карточки товаров)
- Чат-менеджер без звонков
- Ассистент / помощник руководителя
- Парсинг / аналитика
- Удалёнка, remote

### OUT (точно не наше)
- Reels / видео-монтаж / motion / 3D / After Effects
- Call-центр / холодные звонки / обзвон / sales
- ML-разработчик / data scientist / QA
- Без опыта + низкая ЗП
- Микрозадачи, оплата за лайки
- MLM, сетевой маркетинг
- Генерация картинок (midjourney для контента)

## Структура вывода

```json
{
  "scanned_at": "2026-06-19T08:30:00+0300",
  "total": 12,
  "by_decision": {"IN": 5, "REVIEW": 4, "OUT": 3},
  "vacancies": [
    {
      "channel": "-1001485944670",
      "message_id": "15122",
      "text": "ищу маркетолога авто воронок...",
      "contact": "@anna_xzu",
      "contact_kind": "telegram",
      "score": 0.45,
      "matched_keywords": ["воронок", "ai", "удалён"],
      "decision": "IN",
      "reasons": []
    }
  ]
}
```

## Известные ограничения

- Требует открытый Web A в persistent Chrome (порты 9222 helper, 9223 worker)
- Virtual scroll — парсит только видимые в момент посты
- Кросс-домен iframe (chatik) не парсится — это отдельный сервис
- Не отправляет сообщения — только парсит. Для отправки см. skill
  `send-tg-message-cdp`
- Воркеры: основное использование в `Worker` профиле (Hermes),
  в `Helper` профиле этот скрипт не запускается автоматически
