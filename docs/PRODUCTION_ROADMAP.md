# TATTOTOYOU — roadmap до production-пилота

Актуально на 2026-07-09.

Проект находится на стадии **late product MVP**: Telegram/AI-контур, RAG,
PostgreSQL Inbox/Outbox, handoff, панель Сони, заявки и расписание уже работают.
Цель следующих этапов — не наращивать количество функций, а доказать
надёжность, наблюдаемость и бизнес-полезность на закрытом Telegram-пилоте.

## P0. Production Gate

Статус: **реализовано и подтверждено зелёным GitHub Actions**.

- [x] зависимости фиксируются в `uv.lock`;
- [x] unit-тесты запускаются на Python 3.11 и 3.12;
- [x] coverage baseline поднят до 74%;
- [x] Alembic graph обязан иметь одну base и один head;
- [x] PostgreSQL 15 + pgvector и Redis поднимаются в CI;
- [x] миграции применяются к пустой БД;
- [x] PostgreSQL integration-тесты запускаются в CI;
- [x] production/development Compose проходят config smoke;
- [x] production image собирается из lock-файла;
- [x] подтверждён зелёный прогон обновлённого workflow в GitHub Actions;
- [ ] поднять unit coverage с 74% до 80%, затем до 85%;
- [ ] погасить type debt и сделать strict mypy блокирующим gate.

Definition of Done: каждый зелёный commit гарантирует корректный lint, unit
baseline, линейную историю миграций, PostgreSQL-инварианты и сборку контейнера.

## P1. AI quality и безопасная эскалация

Статус: **основной safety-контур реализован в текущем патче**.

- [x] опасные substring-правила заменены на explainable regex/word-boundary rules;
- [x] собран regression dataset из 169 клиентских фраз;
- [x] classifier возвращает intent, confidence, matched rules и score breakdown;
- [ ] считать precision/recall на независимой выборке реальных диалогов;
- [x] добавлен консервативный prompt-injection detector;
- [x] расширена эскалация: несовершеннолетние, перенос/отмена, сотрудничество,
  VIP, чувствительные зоны, явный запрос человека и low confidence;
- [x] отсутствие подтверждённого RAG-знания продолжает переводить диалог Соне;
- [x] terminal fallback проводится через Transactional Outbox;
- [x] уведомления Сони долговечны и идемпотентны через notification outbox;
- [x] причина эскалации сохраняется в notification payload и structured logs;
- [ ] добавить Prometheus-метрики причин эскалации;
- [ ] создать независимый RAG evaluation dataset с ожидаемым FAQ и решением
  auto/handoff;
- [ ] добавить политику повторных неудачных ответов в рамках одного диалога.

Definition of Done: чувствительные обращения не получают автономный ответ без
подтверждённого знания; regression dataset проходит в CI.

## P2. Целостность записи и денег

Статус: **основной блок реализован в текущем патче**.

- [x] форма расписания больше не присваивает произвольный `deposit_status`;
- [x] переходы предоплаты проходят только через `DEPOSIT_TRANSITIONS`;
- [x] сумма обязательна для `pending`, `paid`, `refunded`;
- [x] предоплата не может превышать согласованную цену;
- [x] `refunded` разрешён только после отмены записи;
- [x] `confirmed`/`completed` требуют `paid` или `not_required`;
- [x] правила продублированы PostgreSQL CHECK constraints;
- [x] `confirmed_at` сохраняет время первоначального подтверждения;
- [x] добавлена очередь отменённых записей с невозвращённой предоплатой;
- [x] автоматические промежуточные этапы журнала помечаются `actor=system`;
- [x] push-CI запускается для реальной ветки `dev`;
- [ ] добавить структурированные причины потери заявки;
- [ ] перейти от одной записи к нескольким сеансам одного тату-проекта;
- [ ] добавить SLA и автоматическое напоминание по невыполненному возврату.

Definition of Done: некорректное финансовое состояние нельзя создать через UI,
сервисный слой или прямую запись в PostgreSQL.

## P3. Observability и эксплуатация

- [ ] подключить Sentry SDK;
- [ ] добавить Prometheus endpoint;
- [ ] метрики latency/error/retry для Telegram и GigaChat;
- [ ] метрики backlog и возраста старейших Inbox/Outbox записей;
- [ ] alerts на failed delivery, зависшие processing/sending и readiness;
- [ ] dashboard: AI automation rate, escalation rate, takeover time;
- [ ] прогрев embedding-модели и readiness её загрузки;
- [ ] документировать recovery и incident runbook.

Definition of Done: сбой обнаруживается мониторингом раньше, чем клиентом или
Соней.

## P4. Deployment и закрытый Telegram-пилот

- [ ] VPS, домен, HTTPS и reverse proxy;
- [ ] production secrets и ротация ключей;
- [ ] firewall и закрытые PostgreSQL/Redis;
- [ ] автоматический deploy и rollback;
- [ ] ежедневный backup PostgreSQL;
- [ ] проверка восстановления backup;
- [ ] AI kill switch;
- [ ] загрузка подтверждённой Соней базы знаний;
- [ ] пилот на ограниченном числе реальных диалогов.

Definition of Done: нет потерянных событий и необъяснимых дублей, Соня работает
с панелью без помощи разработчика, а backup реально восстанавливается.

## P5. Рабочее место Сони

- [ ] приём Telegram photo/document/media group;
- [ ] автоматическая привязка референсов к заявке;
- [ ] безопасное хранение файлов и retention policy;
- [ ] UI управления базой знаний;
- [ ] рабочий календарь: дни, перерывы, отпуск, буфер между сеансами;
- [ ] перенос записи с историей;
- [ ] напоминания клиентам;
- [ ] audit log действий оператора;
- [ ] отдельные пользователи, отзыв сессий, login rate limit и MFA;
- [ ] security headers: CSP, HSTS, frame protection и Referrer-Policy.

## P6. Бизнес-аналитика

- [ ] время первого ответа AI и Сони;
- [ ] доля AI-only диалогов;
- [ ] conversion: диалог → заявка → подтверждённый сеанс;
- [ ] средний чек, отмены, no-show и причины потери;
- [ ] FAQ без релевантного ответа;
- [ ] CSAT после завершённого обращения.

## P7. Мультиплатформенность

Порядок: **VK после стабильного Telegram-пилота, Instagram последним**.

- [ ] общий `NormalizedIncomingEvent` и contract tests адаптеров;
- [ ] VK Callback API, подпись, attachments и delivery errors;
- [ ] Instagram Business/Meta approval, webhook и media flow;
- [ ] платёжный провайдер только после проверки ручной модели предоплаты;
- [ ] Vision-анализ — после надёжного хранения и просмотра референсов.

## Следующий инженерный блок

После зелёного CI текущего патча: **P3 Observability и эксплуатация** — Sentry,
Prometheus, backlog/latency/error metrics, alerts и readiness embedding-модели.
Параллельно накапливается независимая выборка реальных диалогов для измерения
precision/recall без подгонки под regression rules.
