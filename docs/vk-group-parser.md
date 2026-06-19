# VK Group Wall Parser

Парсер вакансий из стены группы ВКонтакте через **persistent Chrome DevTools Protocol**.

## Зачем

Стандартные VK-парсеры (vk_api, requests к API) требуют:
- service token или OAuth-токен пользователя
- обход защиты от ботов
- решение капчи при логине
- rate-limit (~3 RPS в лучшем случае)

**Этот парсер использует уже авторизованную Web-сессию VK** в persistent
Chrome profile. Никаких credentials, никаких токенов, никаких банов —
парсим группу, на которую уже подписаны.

## Как работает

1. Persistent Chrome уже запущен на порту 9223 (см. skill
   `chrome-cdp-persistent-profile`).
2. Парсер подключается к открытой вкладке VK через CDP.
3. Резолвит короткое имя группы (`distantsiya` → `72829428`).
4. Открывает стену группы, скроллит, чтобы подгрузить ленивый контент.
5. Извлекает тексты постов через `[data-post-id]` и `[class*="wall_item"]`.
6. Классифицирует по нишевым IN/OUT-ключевым словам.
7. Извлекает контакты (@username, t.me, email, phone, max).
8. Возвращает JSON с вакансиями + рекомендациями.

## Установка

```bash
pip install websocket-client
```

Persistent Chrome должен быть запущен, вкладка с vk.com открыта и залогинена.

## Использование

```bash
# Парсить группу по короткому имени
python scripts/vk_group_parser.py --group distantsiya

# По числовому id группы
python scripts/vk_group_parser.py --group 72829428

# Последние 30 постов, только IN-вакансии
python scripts/vk_group_parser.py --group distantsiya --last 30 --filter in

# Сохранить в файл
python scripts/vk_group_parser.py --group distantsiya --out vacancies.json
```

## Нишевой фильтр

Совпадает с `tg_channel_parser.py` — единый словарь IN/OUT для всех парсеров
портафолио:

### IN (наши ниши)
- Автоворонки, лид-магниты, email-цепочки
- SMM / контент-план / копирайтинг
- AI / Claude / GPT / нейросети
- Telegram-боты, AI-агенты, автоматизация
- WB / Ozon / маркетплейсы (карточки товаров)
- Чат-менеджер без звонков
- Ассистент / помощник руководителя / бизнес-ассистент
- Парсинг / аналитика
- Удалёнка, remote

### OUT (точно не наше)
- Reels / видео-монтаж / motion / 3D / After Effects
- Call-центр / холодные звонки / обзвон / sales
- B2B-продажи / активные продажи / личный план продаж
- ML-разработчик / data scientist / QA
- Без опыта + низкая ЗП
- Микрозадачи, оплата за лайки
- MLM, сетевой маркетинг
- Генерация картинок (midjourney для контента)

## Структура вывода

```json
{
  "scanned_at": "2026-06-19T10:30:00+0300",
  "group": "distantsiya",
  "group_id": "72829428",
  "total": 8,
  "by_decision": {"IN": 2, "REVIEW": 3, "OUT": 3},
  "vacancies": [
    {
      "group": "distantsiya",
      "post_id": "-40527789_244083",
      "text": "ВАКАНСИЯ: КОНТЕНТ-ПРОДЮСЕР...",
      "contact": "@Tubis_work",
      "contact_kind": "telegram",
      "score": 0.6,
      "matched_keywords": ["контент", "smm", "копирайт"],
      "decision": "IN",
      "reasons": []
    }
  ]
}
```

## Известные ограничения

- Требует открытый VK в persistent Chrome (порт 9223)
- Virtual scroll — парсит только видимые/подгруженные посты (скрипт
  скроллит страницу 8 раз для загрузки ~20 последних постов)
- Если VK в фокусе не активен, может потребоваться reload вкладки
- Anti-spam: max 3 отклика/группа/день (правило из work-checklist)
- Не отправляет сообщения — только парсит

## Связь с tg_channel_parser.py

Оба парсера используют **общий нишевой IN/OUT словарь** и одинаковую структуру
выходного JSON (`by_decision`, `score`, `matched_keywords`). Это позволяет:

- Писать общие downstream-скрипты (фильтрация, отправка откликов, дашборд)
- Использовать единый шаблон для добавления новых платформ
- Сравнивать «signal quality» между каналами (TG vs VK vs Avito)