#!/usr/bin/env python3
"""
vacancy_scorer.py — ранкер вакансий по автоматизируемости и релевантности.

Использование:
    cat vacancies.json | python3 vacancy_scorer.py            # JSON в stdout
    cat vacancies.json | python3 vacancy_scorer.py --top 10  # топ-10
    cat vacancies.json | python3 vacancy_scorer.py --md      # markdown таблица

На вход — JSON массив объектов:
    [{"title": "...", "salary": "...", "company": "...", "desc": "..."}, ...]

Что оценивает (0-100, затем сумма с весами):
    1. auto_friendly   (50%) — насколько легко автоматизировать "как человек"
    2. salary_fit      (15%) — попадание в вилку 30-100к
    3. remote_flag     (15%) — удалёнка / гибрид / офис
    4. niche_match     (10%) — попадание в наши ниши (SMM, контент, AI, e-com, ассистент)
    5. time_to_value   (10%) — насколько быстро можно показать результат

Без внешних зависимостей. Python 3.8+.
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional

# ---------- конфиг скоринга ----------

SALARY_MIN = 30_000   # нижняя граница вилки
SALARY_MAX = 100_000  # верхняя граница (выше — штрафуем как "не наша вилка")
SALARY_SWEET_LOW = 60_000  # идеально — от 60к

# Паттерны для скоринга "автоматизируемости"
AUTO_HIGH = re.compile(
    r"(парсинг|автоматиз\w+|ai[\s\-]?агент|n8n|telegram[\s\-]?бот|воронк\w+|"
    r"аналит\w+|отчёт\w+|скрипт\w+|складск\w+|остатк\w+|оборачив\w+|"
    r"api\s|интеграц\w+|контент[\s\-]план|копирайт\w+|редакт\w+|"
    r"модерац\w+|ассист\w+|операт\w+\s+маркетплейс|карточк\w+\s+товар\w+|"
    r"контент[\s\-]мейкер|email[\s\-]маркетин|автоворонк\w+|"
    r"ai[\s\-]креатор|ai[\s\-]дизайнер|mpstat|abc[\s/]xyz|"
    r"юнит[\s\-]экономи|финанс\w+\s+отчёт\w+|дашборд\w+|sql\w*)",
    re.IGNORECASE,
)
AUTO_MEDIUM = re.compile(
    r"(smm|маркетплейс\w*|ozon|wildberries|wb\W|контент|стать\w+|"
    r"описа\w+|реклам\w+|товар\w+|поставк\w+|анализ\w+|\bseo\W|"
    r"email[\s\-]рассылк\w+|ворон\w+продаж|рекламн\w+\s+кампан\w+|"
    r"тз\W|тендер\w+|поставщ\w+|закуп\w+|рекрут\w+|hr\W|"
    r"личн\w+\s+ассист|бизнес[\s\-]ассист|операт\w+\s+чат\w*|"
    r"чат[\s\-]менедж|чат[\s\-]операт|чат[\s\-]поддерж|"
    r"обучен\w+\s+модел\w+|промпт\w*)",
    re.IGNORECASE,
)

# OUT-фильтр — если срабатывает хоть один, вакансия исключается
OUT_PATTERNS = {
    "calls": re.compile(r"\b(звонк|обзвон|оператор\s+колл|оператор\s+кц|холодн\w*\s+звон|"
                        r"оператор\s+входящ\w+\s+лини|телемаркет|менеджер\s+по\s+холод\w*|"
                        r"обзвон\w+|call[\s\-]?центр|входящ\w+\s+лини\w+|исходящ\w+\s+звон)", re.IGNORECASE),
    "reels": re.compile(r"\b(reels?|рилс\w*|видео\s*продакшн|видеосъём|видеомонтаж|"
                        r"motion\W|анимац\w+\s+видео|видеограф)", re.IGNORECASE),
    "image_gen": re.compile(r"\b(генерац\w*\s*картин|генерац\w*\s*изображ|"
                            r"рисов\w*\s+иллюстр|созда\w*\s+иллюстр|нейро[\s\-]иллюстр|"
                            r"рис\w+\s+с\s+нуля|арт\W)", re.IGNORECASE),
    "sales": re.compile(r"\b(менеджер\s+по\s+продаж|руководитель\s+отдела\s+продаж|"
                        r"холодн\w*\s+продаж|оптов\w*\s+продаж|менеджер\s+активных\s+продаж|"
                        r"менеджер\s+продаж\s+опыт|менеджер\s+продаж\s+b2b|"
                        r"директор\s+по\s+продаж|стрит[\s\-]ритейл|риелтор\W)", re.IGNORECASE),
    "mlm": re.compile(r"\b(млм|mlm|сетевой\s+маркетинг|сетев\w+\s+бизнес)", re.IGNORECASE),
    "microtasks": re.compile(r"(за\s+лайк|за\s+отзыв|за\s+подписк\w*|микрозадач\w*|"
                             r"ставь\s+лайк|смотри\s+ролик\w*|став\w+\s+лайк)", re.IGNORECASE),
    "physical": re.compile(r"\b(выезд\w+\s+на\s+объект|выезд\w+\s+оцен|курьер\w+|"
                           r"развоз\w+|доставк\w+\s+товар\w*|обход\w+\s+территор\w*)", re.IGNORECASE),
}

# Ниши — для niche_match score
NICHE_PATTERNS = {
    "ai": re.compile(r"\b(ai|ml|искусственн\w+\s+интеллект|нейросет\w+|машин\w+\s+обучен|"
                     r"автоматиз\w+|ai[\s\-]?агент\w+|ai[\s\-]креатор)", re.IGNORECASE),
    "ecom": re.compile(r"\b(маркетплейс\w*|ozon|wildberries|\bwb\b|карточк\w+\s+товар\w*|"
                       r"\bmpstat|abc[\s/]xyz|поставк\w+\s+на\s+склад|оборачив\w+|"
                       r"юнит[\s\-]экономи|fbo|fbs|селлер\w*)", re.IGNORECASE),
    "content": re.compile(r"\b(smm|контент|копирайт\w+|редакт\w+|сторителлинг\w*|"
                          r"копирайтер|рерайт\w+|блог\w+|пост\w+|сторис|reels?\b|"
                          r"email[\s\-]маркетин\w*|рассыл\w+|ворон\w+|автоворон\w+|"
                          r"контент[\s\-]план\w*|контент[\s\-]мейкер)", re.IGNORECASE),
    "analytics": re.compile(r"\b(аналит\w+|отчёт\w+|дашборд\w+|sql\w*|"
                            r"управленч\w+\s+отчёт|бизнес[\s\-]аналит\w*|финанс\w+\s+аналит\w*)", re.IGNORECASE),
    "assistant": re.compile(r"\b(ассист\w+|личн\w+\s+ассист|бизнес[\s\-]ассист\w*|"
                            r"операт\w+\s+поддержк\w+|чат[\s\-]менедж\w*|чат[\s\-]операт\w*)", re.IGNORECASE),
    "design_static": re.compile(r"\b(инфографик\w*|карточк\w+\s+дизайн\w*|дизайн\w+\s+wb|"
                                r"дизайн\w+\s+ozon|верстк\w+\s+карточ\w*)", re.IGNORECASE),
}

# Удалёнка
REMOTE_RE = re.compile(r"\b(удал[её]н\w*|remote|гибрид\w*|home\s*office|из\s+дома)", re.IGNORECASE)
OFFICE_RE = re.compile(r"\b(офис\w*|в\s+офис\w*|на\s+территор\w+|присутств\w+)", re.IGNORECASE)

# Время до первого результата (кейс)
TIME_FAST = re.compile(r"\b(опыт\s+не\s+обязател\w*|без\s+опыт\w+|обучен\w+\s+с\s+нуля|"
                       r"готов\w*\s+взять\w*\s+и\s+делать|быстр\w+\s+старт|прост\w+\s+задач\w*)",
                       re.IGNORECASE)
TIME_SLOW = re.compile(r"\b(3[\s\-]6\s+лет|более\s+6\s+лет|опыт\s+более\s+3\s+лет|"
                       r"опыт\s+более\s+5\s+лет|опыт\s+от\s+3\s+лет|опыт\s+от\s+5\s+лет)",
                      re.IGNORECASE)


# ---------- скоринг ----------

@dataclass
class Scored:
    title: str
    company: str
    salary: str
    link: str
    score: float
    components: Dict[str, float]
    out_reason: Optional[str] = None
    niches: Optional[List[str]] = None
    notes: str = ""

    def __post_init__(self):
        if self.niches is None:
            self.niches = []


def detect_out_reason(text: str) -> Optional[str]:
    """Возвращает первую причину OUT или None."""
    for reason, pat in OUT_PATTERNS.items():
        if pat.search(text):
            return reason
    return None


def detect_niches(text: str) -> List[str]:
    found = []
    for niche, pat in NICHE_PATTERNS.items():
        if pat.search(text):
            found.append(niche)
    return found


def parse_salary_midpoint(s: str) -> Optional[int]:
    """Достаёт среднее из вилки '60 000 — 90 000 ₽' или 'от 50 000'."""
    if not s:
        return None
    nums = [int(x.replace(" ", "").replace("\u202f", "")) for x in
            re.findall(r"\b(\d[\d\s\u202f]*)\s*(?:000|000)?\b", s) if x.replace(" ", "").replace("\u202f", "").isdigit()]
    # выбираем только реалистичные (>= 5000)
    nums = [n for n in nums if n >= 5000][:4]
    if not nums:
        return None
    if "от" in s.lower() and len(nums) == 1:
        return nums[0]
    if len(nums) >= 2:
        return (min(nums) + max(nums)) // 2
    return nums[0]


def score_auto_friendly(text: str) -> float:
    """0..1 — насколько легко автоматизировать."""
    high = len(AUTO_HIGH.findall(text))
    medium = len(AUTO_MEDIUM.findall(text))
    if high >= 3:
        return 1.0
    if high >= 2:
        return 0.85
    if high >= 1 and medium >= 2:
        return 0.7
    if high >= 1:
        return 0.55
    if medium >= 3:
        return 0.45
    if medium >= 2:
        return 0.35
    if medium >= 1:
        return 0.2
    return 0.05


def score_salary_fit(s: str) -> float:
    """0..1 — попадание в 30-100к."""
    mid = parse_salary_midpoint(s)
    if mid is None:
        return 0.4  # не указана — нейтрально
    if mid < SALARY_MIN:
        return 0.0
    if mid > SALARY_MAX * 1.5:  # слишком высокая — не наша вилка
        return 0.1
    if mid > SALARY_MAX:
        return 0.3
    # идеально 60-100к
    if mid >= SALARY_SWEET_LOW:
        return 1.0
    # 30-60к — норм
    return 0.5 + 0.5 * (mid - SALARY_MIN) / (SALARY_SWEET_LOW - SALARY_MIN)


def score_remote(text: str) -> float:
    if REMOTE_RE.search(text):
        if OFFICE_RE.search(text) and "гибрид" not in text.lower():
            return 0.5
        return 1.0
    if OFFICE_RE.search(text):
        return 0.1
    return 0.5  # не указано — нейтрально


def score_niche(text: str) -> float:
    """0..1 — попадание в ниши."""
    found = detect_niches(text)
    if not found:
        return 0.1
    if "ai" in found or "ecom" in found:
        return 1.0
    if len(found) >= 2:
        return 0.85
    return 0.6


def score_time_to_value(text: str) -> float:
    """0..1 — быстрое начало работы."""
    if TIME_FAST.search(text):
        return 1.0
    if TIME_SLOW.search(text):
        return 0.2
    return 0.6


WEIGHTS = {
    "auto_friendly": 0.50,
    "salary_fit":    0.15,
    "remote":        0.15,
    "niche":         0.10,
    "time_to_value": 0.10,
}


def score_one(v: Dict[str, Any]) -> Scored:
    text = " ".join(str(v.get(k, "")) for k in ("title", "company", "desc", "salary"))
    out_reason = detect_out_reason(text)
    if out_reason:
        return Scored(
            title=v.get("title", ""),
            company=v.get("company", ""),
            salary=v.get("salary", ""),
            link=v.get("href", v.get("link", "")),
            score=0.0,
            components={k: 0.0 for k in WEIGHTS},
            out_reason=out_reason,
            niches=[],
            notes="filtered out",
        )
    components = {
        "auto_friendly": score_auto_friendly(text),
        "salary_fit":    score_salary_fit(v.get("salary", "")),
        "remote":        score_remote(text),
        "niche":         score_niche(text),
        "time_to_value": score_time_to_value(text),
    }
    total = sum(components[k] * WEIGHTS[k] for k in WEIGHTS) * 100
    niches = detect_niches(text)
    return Scored(
        title=v.get("title", ""),
        company=v.get("company", ""),
        salary=v.get("salary", ""),
        link=v.get("href", v.get("link", "")),
        score=round(total, 1),
        components={k: round(v_, 2) for k, v_ in components.items()},
        niches=niches,
    )


def render_table_md(items: List[Scored]) -> str:
    rows = ["| # | Score | Title | Company | Salary | Niches |",
            "|---|-------|-------|---------|--------|--------|"]
    for i, s in enumerate(items, 1):
        title = s.title.replace("|", "/").replace("\n", " ")[:80]
        company = s.company.replace("|", "/").replace("\n", " ")[:40]
        salary = s.salary.replace("|", "/").replace("\n", " ")[:30]
        niches = ", ".join(s.niches) if s.niches else "—"
        rows.append(f"| {i} | {s.score:.0f} | {title} | {company} | {salary} | {niches} |")
    return "\n".join(rows)


def render_json(items: List[Scored]) -> str:
    return json.dumps([asdict(s) for s in items], ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="Score vacancies by auto-friendliness + niche")
    ap.add_argument("--top", type=int, default=None, help="show only top N")
    ap.add_argument("--md", action="store_true", help="markdown table output")
    ap.add_argument("--show-out", action="store_true", help="include filtered-out entries")
    args = ap.parse_args()

    raw = sys.stdin.read().strip()
    if not raw:
        sys.stderr.write("empty input\n")
        sys.exit(2)
    try:
        vacancies = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"invalid JSON: {e}\n")
        sys.exit(2)

    if not isinstance(vacancies, list):
        sys.stderr.write("expected JSON array\n")
        sys.exit(2)

    scored = [score_one(v) for v in vacancies]
    scored.sort(key=lambda s: s.score, reverse=True)

    visible = scored
    if not args.show_out:
        visible = [s for s in scored if not s.out_reason]
    if args.top:
        visible = visible[: args.top]

    if args.md:
        print(render_table_md(visible))
    else:
        print(render_json(visible))

    # summary в stderr
    total = len(scored)
    kept = len([s for s in scored if not s.out_reason])
    by_out: Dict[str, int] = {}
    for s in scored:
        if s.out_reason:
            by_out[s.out_reason] = by_out.get(s.out_reason, 0) + 1
    sys.stderr.write(f"total: {total}, kept: {kept}, out: {total - kept}\n")
    if by_out:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(by_out.items(), key=lambda kv: -kv[1]))
        sys.stderr.write(f"out by: {parts}\n")


if __name__ == "__main__":
    main()