#!/usr/bin/env python3
"""
tg_scam_checker.py — детектор scam-сообщений в Telegram по тексту + meta аккаунта.

Используется воркер-профилем при ручном чек-листе: вместо того чтобы
самому вспоминать все red flags (Not a contact, аккаунт создан недавно,
автоматическая рассылка через бот, шаблонный текст, подозрительная
ссылка, "без опыта + обучение бесплатно", маленькая оплата), скармливаем
текст + meta сюда и получаем вердикт: SCAM / REAL / UNKNOWN.

Покрывает паттерны из 21.06.2026 (TG: Stanislαv Antonov, Eugene Carney
@PostOnSteroidsBot) и 22.06.2026 (TG: Елена Менеджер с ИНН/Альфа-Банк).

Использование:
    from tg_scam_checker import check_tg_message
    verdict, reasons = check_tg_message(
        text="Здравствуйте! Пишу Вам от компании MPNext. 380₽/час...",
        account_meta={
            "is_contact": False,
            "registration": "June 2026",       # из профиля TG
            "via_bot": "@PostOnSteroidsBot",   # если видно "via @botname"
        },
    )
    # verdict: 'scam' | 'real' | 'unknown'
    # reasons: ['new_account', 'auto_via_bot', 'microtask_pay', ...]

CLI:
    python3 tg_scam_checker.py "Здравствуйте! 380₽/час, без опыта..."
"""
import re
import sys
from typing import Tuple, List, Dict


# ─── Red-flag паттерны ────────────────────────────────────────────────────

# 1. Подозрительные ссылки (внешние, особенно shorteners / random домены)
SUSPICIOUS_LINK_DOMAINS = re.compile(
    r"https?://("
    r"[a-z0-9-]+\.site|"
    r"bit\.ly|t\.co|goo\.gl|is\.gd|clck\.ru|"
    r"tinyurl\.com|ow\.ly|"
    r"[a-z0-9-]+\.online|[a-z0-9-]+\.xyz|[a-z0-9-]+\.top"
    r")",
    re.IGNORECASE,
)

# 2. Запрос чувствительных данных (ИНН, паспорт, карта)
SENSITIVE_DATA_REQUEST = re.compile(
    r"(пришл[ите].{0,30}(инн|паспорт|карт|снилс)|"
    r"привяз[аи].{0,30}(сч[её]т|карт|банк)|"
    r"корпоративн\w+\s+сч[её]т|"
    r"персональн\w+\s+слот|"
    r"налог\s+6\s*%|"
    r"автоматическ\w+\s+привяз[аи])",
    re.IGNORECASE,
)

# 3. Микрозадачи / низкая оплата
MICROTASK_PAY = re.compile(
    r"(\bот\s+200\s*₽/час\b|\bот\s+300\s*₽/час\b|\bот\s+380\s*₽/час\b|"
    r"\bот\s+400\s*₽/час\b|"
    r"\bза\s+лайк|\bза\s+отзыв|\bза\s+подписк|"
    r"\bбез\s+опыта\b.*\bобучен|\bобучен\w+\s+бесплатн|"
    r"количество\s+мест\s+ограничено)",
    re.IGNORECASE,
)

# 4. Шаблонные признаки массовой рассылки
MASS_MAILING_SIGNALS = re.compile(
    r"(автоматическ\w+\s+рассылк|"
    r"на\s+вопросы\s+ответить\s+не\s+смогу|"
    r"если\s+вам\s+(вс[её]\s+ещ[её]|вс[её]\s+ещ[её])\s+актуальн|"
    r"пишу\s+вам\s+потому\s+что\s+откликались|"
    r"здравствуйте!?\s*пишу\s+вам\s+от\s+компании)",
    re.IGNORECASE,
)

# 5. Бот-репост (через @botname)
BOT_REPOST = re.compile(r"\bvia\s+@\w+bot\b|@PostOnSteroidsBot", re.IGNORECASE)


# ─── Скоринг ─────────────────────────────────────────────────────────────

def check_tg_message(text, account_meta=None):
    """
    Возвращает (verdict, reasons).
    verdict: 'scam' если ≥2 strong flags, 'unknown' если 1, 'real' если 0.
    reasons: список имён сработавших правил.
    """
    account_meta = account_meta or {}
    reasons = []

    # ── Account-level ──
    if account_meta.get("is_contact") is False:
        # Сам по себе не red flag, но вместе с другими — да
        reasons.append("not_a_contact")

    reg = (account_meta.get("registration") or "").lower()
    if reg:
        # Аккаунт < 6 мес → подозрительно
        month_map = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        for name, num in month_map.items():
            if name in reg:
                import datetime
                now = datetime.date(2026, 6, 22)  # knowledge cutoff
                # Если регистрация в этом году и месяц >= текущий → свежий
                if "2026" in reg and num >= now.month:
                    reasons.append("new_account_2026")
                elif "2026" in reg and num >= now.month - 3:
                    reasons.append("new_account_recent")
                break

    if BOT_REPOST.search(text or ""):
        reasons.append("auto_via_bot")

    # ── Content-level ──
    if SUSPICIOUS_LINK_DOMAINS.search(text or ""):
        reasons.append("suspicious_link")

    if SENSITIVE_DATA_REQUEST.search(text or ""):
        reasons.append("sensitive_data_request")

    if MICROTASK_PAY.search(text or ""):
        reasons.append("microtask_pay_or_mlm")

    if MASS_MAILING_SIGNALS.search(text or ""):
        reasons.append("mass_mailing_template")

    # ── Verdict ──
    strong_flags = {
        "auto_via_bot", "sensitive_data_request", "microtask_pay_or_mlm",
        "suspicious_link",
    }
    weak_flags = {
        "not_a_contact", "new_account_2026", "new_account_recent",
        "mass_mailing_template",
    }
    has_strong = bool(strong_flags & set(reasons))
    weak_count = len(weak_flags & set(reasons))

    if has_strong or weak_count >= 2:
        verdict = "scam"
    elif reasons:
        verdict = "unknown"
    else:
        verdict = "real"

    return verdict, reasons


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        text = sys.argv[1]
        verdict, reasons = check_tg_message(text)
        print(f"verdict: {verdict}")
        print(f"reasons: {reasons}")
    else:
        # Demo: проверяем три кейса из реальных сессий
        cases = [
            (
                "Здравствуйте! Пишу Вам от компании MPNext. 380₽/час, без опыта, "
                "обучение бесплатное. https://promo247.site/kgCHQTmK",
                {"is_contact": False, "registration": "June 2026", "via_bot": "@PostOnSteroidsBot"},
            ),
            (
                "Благодарим за интерес. Пару слов о работе: генерить картинки в ChatGPT, "
                "тестовое обязательно. Пришлите резюме на yesyesbrand@mail.ru",
                {"is_contact": True, "registration": "March 2025"},
            ),
            (
                "Елена Менеджер updated photo on 1/21/1970. "
                "Оформление: договор ИП, налог 6%. Привязка к Альфа-Банк, "
                "корпоративный счёт, персональный слот. Пришлите ИНН.",
                {"is_contact": False, "registration": "March 2025"},
            ),
            (
                "Здравствуйте, Илья! Спасибо за отклик. Можем созвониться завтра в 14:00?",
                {"is_contact": True, "registration": "April 2024"},
            ),
        ]
        for i, (text, meta) in enumerate(cases, 1):
            v, r = check_tg_message(text, meta)
            print(f"\n=== Case {i} ===")
            print(f"text: {text[:80]}...")
            print(f"meta: {meta}")
            print(f"verdict: {v}, reasons: {r}")


if __name__ == "__main__":
    main()
