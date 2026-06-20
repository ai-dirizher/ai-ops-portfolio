# Changelog

All notable changes to ai-ops-portfolio.

## [2026-06-20]

- `feat(scripts)`: hh_auto_responder.py — автоответ на входящие HH-чаты через CDP
  с защитой от дублей (pre-check + native click + post-check).
  Три уровня защиты от дублей на основе реального бага, пойманного
  в продакшене (dispatchEvent MouseEvent = 2 одинаковых сообщения).
  Шаблонизатор ответов по эталону "Дирижёр Нейросетей": анкета / собес /
  ставка / тестовое / общее.

## [2026-06-19]

- `feat(scripts)`: vacancy_scorer.py — ранкер вакансий по 5 критериям
  (auto 50% / salary 15% / remote 15% / niche 10% / time 10%).

## [2026-06-17]

- Daily automated update
