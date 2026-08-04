# Апгрейд стека логирования: Promtail → Alloy, Loki 3.4 → 3.7

Дата: 2026-08-04. Область: `bks/monitoring`.
Задача архитектурного улучшения #9 (Phase 3, приоритет medium).

---

## 1. Аудит текущего стека

Снято с работающего хоста (`docker ps`, конфиги в `monitoring/`, живые
запросы к Loki), а не с документации.

| Компонент | Было | Актуальная версия на 2026-08-04 | Статус |
|---|---|---|---|
| `grafana/loki` | 3.4.2 | 3.7.4 | отставание на 3 minor |
| `grafana/promtail` | 3.4.2 | 3.6.11 (последний) | **компонент deprecated** |
| `grafana/grafana` | 12.0.2 | 13.1.1 | вне области (см. §5) |
| `prom/prometheus` | v3.4.1 | v3.13.2 | вне области (см. §5) |

Источники логов (все три — JSONL, читаются с файловой системы):

| Поток (job) | Файл | Объём за 30 дней | Содержимое |
|---|---|---|---|
| `security-audit` | `/var/log/router/security_audit.jsonl` | 4 строки | текст запроса + уровень риска |
| `router-audit` | `/var/log/router/audit.jsonl` | 247 строк | учёт трафика без текста |
| `bks-watchdog` | `/var/log/watchdog/metrics.jsonl` | 81 135 строк | 9 health-проверок каждые 5 мин |

### 1.1 Что нашлось кроме отставания версий

**Promtail снят с поддержки.** Grafana объявила Promtail deprecated в
феврале 2025, поддержка (включая security-фиксы) закончилась в марте
2026. То есть «обновить до последней версии» для шиппера логов означает
не смену тега, а миграцию на Alloy — официального преемника.

**Уровень логов определялся эвристикой и определялся неверно.** Loki 3.x
сам проставляет `detected_level`. Живой замер показал, чем это
оборачивается на этих данных:

- `bks-watchdog` — все 81 135 строк с `detected_level="unknown"`, хотя
  каждая запись несёт явное поле `status` (`ok`/`fail`);
- `security-audit` — записи разъехались между `error` и `info` по
  случайным словам внутри `content_excerpt` (текст запроса пользователя),
  при том что в записи есть числовое поле `level` со шкалой риска.

**Время записи не использовалось.** В `promtail-config.yml` не было ни
одной `stage.timestamp`, поэтому Loki ставил записям время приёма. При
любом рестарте шиппера или бэкфилле файла вся история получала «сейчас».

**Фильтровать было нечем.** Ни одно поле записей не было доступно как
измерение: любой отбор требовал `| json` по всему потоку на чтении.
Вынести поля в stream-лейблы было нельзя сознательно — для изменяемых
значений это анти-паттерн Loki (комментарий об этом стоял прямо в
`promtail-config.yml`).

**Шиппер не мониторился.** В `prometheus.yml` не было job'а для
promtail — «логи не доезжают» обнаруживалось только по пустым панелям.

**Проверка в CI не проверяла доставку.** Пост-деплойный шаг смотрел
наличие job-лейбла в Loki. Лейбл остаётся в индексе и после того, как
доставка встала, поэтому мёртвый шиппер такую проверку проходил.

---

## 2. План апгрейда

Выбранный путь и отвергнутые альтернативы:

| Решение | Выбрано | Почему не иначе |
|---|---|---|
| Шиппер | Grafana Alloy v1.18.0 | Оставаться на promtail — жить без security-фиксов. Vector/Fluent Bit — смена вендора и переписывание пайплайна без выгоды |
| Конвертация конфига | штатный `alloy convert --source-format=promtail` | Ручной перевод рискует потерять деталь; конвертер сам сохранил `legacy_positions_file` — ключ к миграции без дублей |
| Новые измерения | structured metadata | В stream-лейблы нельзя (кардинальность); оставить только `| json` — значит не решить исходную задачу фильтрации |
| Loki | 3.7.4 | Structured metadata требует schema v13 + tsdb — уже были настроены, миграции схемы не нужно |
| Grafana/Prometheus | не трогать | См. §5 |

Порядок работ: конвертировать конфиг → добавить фильтрующий слой →
поднять Loki → проверить на изолированном стеке → обновить CI и доки →
задеплоить через GitLab CI.

---

## 3. Что сделано

`monitoring/alloy/config.alloy` (новый, заменил `promtail/promtail-config.yml`):

1. `stage.timestamp` — время из `ts` (float epoch) для router-потоков и из
   `ts_ms` (целые миллисекунды) для watchdog. `ts_ms` выбран потому, что
   все 9 проверок одного прогона пишутся с одинаковым секундным `ts`.
2. `stage.structured_metadata` — `check`, `status`, `tier`, `provider`,
   `profile`, `category`, `risk` и `level`.
3. `level` из данных: `status=ok → info`, иначе `error` для watchdog;
   риск `3 → error`, `2 → warn`, `1 → info` для security-audit (шкала из
   `router/classifier.py`, `SECURITY_PROMPT`: 0=CLEAR, 1=LOW, 2=MEDIUM,
   3=HIGH); `info` явно для router-audit, у которого severity нет.
4. `stage.drop` — пустые строки (`empty_line`) и не-JSON (`not_jsonl`) с
   отдельными счётчиками, чтобы отсев был виден, а не молчал.

Stream-лейблы (`job`, `filename`) оставлены дословно: на `job` завязаны
панели `bks-health.json` и `bks-router.json`.

`monitoring/loki/loki-config.yml`:

- `retention_stream` — {job="security-audit"} хранится 90 дней
  вместо базовых 30: единственный поток с текстом запросов и единственный,
  по которому разбирают инциденты постфактум, при этом самый малообъёмный
  (4 строки против 81 тысячи у watchdog). Срок задан явно и обоснован —
  правило комплаенса LOG-001;
- `allow_structured_metadata: true` явно — без него весь фильтрующий слой
  молча деградирует;
- `volume_enabled: true` — гистограмма объёма в Grafana Explore, чтобы
  сужать выборку до чтения строк;
- `pattern_ingester.enabled: true` — вкладка Patterns, чтобы десятки тысяч
  однотипных строк watchdog сворачивались по форме.

`monitoring/prometheus/prometheus.yml` — job `alloy` на `alloy:12345`
(self-мониторинг шиппера, которого не было).

`monitoring/.gitlab-ci.yml`:

- новый job `config-validate` — конфиги проверяются родными бинарями
  (`loki -verify-config`, `alloy validate`), а не только `yaml.safe_load`;
  теги образов вычитываются из `docker-compose.yml`, чтобы валидация не
  разъехалась с тем, что деплоится;
- пост-деплойная проверка смотрит счётчик `loki_write_sent_entries_total`
  самого Alloy и падает, если он ноль, — вместо наличия job-лейбла.

---

## 4. Проверка доставки

Продовый стек `monitoring` деплоит GitLab CI, поэтому проверка велась на
**изолированном стеке** (`docker compose -p logupgrade-test`: свой проект,
своя сеть, порты наружу не публиковались). Фикстуры монтировались ровно в
те пути, что прописаны в боевом `config.alloy` (`/var/log/router`,
`/var/log/watchdog`), поэтому проверялся немодифицированный продовый
конфиг, а не его копия.

Фикстуры: 3 валидные записи security-audit (риск 3/2/1) + пустая строка +
строка «не JSON»; 2 записи router-audit; 2 записи watchdog (`ok` и `fail`).
Все с временем на 3–14 минут в прошлом — чтобы отличить событийное время
от времени приёма.

### 4.1 Фильтрация по structured metadata (без парсера) — 14/14 PASS

| Запрос | Ожидалось | Получено |
|---|---|---|
| `{job="bks-watchdog"} | status="fail"` | 1 | 1 |
| `{job="bks-watchdog"} | status="ok"` | 1 | 1 |
| `{job="bks-watchdog"} | check="disk_space"` | 1 | 1 |
| `{job="bks-watchdog"} | level="error"` | 1 | 1 |
| `{job="bks-watchdog"} | level="info"` | 1 | 1 |
| `{job="security-audit"} | level="error"` | 1 | 1 |
| `{job="security-audit"} | level="warn"` | 1 | 1 |
| `{job="security-audit"} | level="info"` | 1 | 1 |
| `{job="security-audit"} | category="injection"` | 1 | 1 |
| `{job="security-audit"} | profile="director-bot"` | 3 | 3 |
| `{job="router-audit"} | provider="nvidia"` | 1 | 1 |
| `{job="router-audit"} | tier="cheap"` | 1 | 1 |
| `{job="router-audit"} | level="info"` | 2 | 2 |
| `{job="security-audit"}` (мусор отсеян) | 3 из 5 строк | 3 |

Уровни выведены из данных корректно во всех случаях: `status=fail → error`,
`status=ok → info`, риск `3 → error`, `2 → warn`, `1 → info`.

### 4.2 Событийное время — PASS

Расхождение между временем записи в Loki и `ts`/`ts_ms` самой записи —
`0.000000 с` на всех 7 записях, при том что фикстуры лежали на 436–855
секунд в прошлом. Если бы `stage.timestamp` не работал, расхождение было
бы равно этому сдвигу. Дробный Unix-epoch (`1785747681.31`) парсится
корректно.

### 4.3 Кардинальность не выросла — PASS

Лейблы в индексе Loki после прогона: `filename`, `job`, `service_name`
(последний Loki добавляет сам). Ни одно из новых измерений в stream-лейблы
не попало.

### 4.4 Счётчики Alloy — PASS

```
loki_process_dropped_lines_total{component_id="loki.process.security_audit",reason="empty_line"} 1
loki_process_dropped_lines_total{component_id="loki.process.security_audit",reason="not_jsonl"}  1
loki_write_sent_entries_total{component_id="loki.write.default"}                                 7
loki_write_dropped_entries_total{...}                                                            0
```

7 доставленных = 3 + 2 + 2 валидные записи. Отсеяно ровно 2 мусорные, с
разбивкой по причине. Потерь при push нет.

### 4.5 Регрессия, найденная прогоном: distroless-образ Loki

Образ `grafana/loki:3.7.4` — distroless: в нём нет ни `wget`, ни `curl`,
ни shell (в 3.4.2 `wget` был). Все три пост-деплойные проверки в
`monitoring/.gitlab-ci.yml` ходили через `docker exec monitoring-loki
wget ...` и после bump'а перестали бы работать в принципе — деплой падал
бы на здоровом стеке.

Исправлено: пробником стал `monitoring-grafana` — он в той же сети и
содержит `curl`. Заодно `http://localhost:3100` заменён на
`http://loki:3100`: «localhost» работал только изнутри самого
loki-контейнера. Проверено на живом контейнере — Grafana достаёт Loki API
по Docker DNS.

Прочих обращений к `monitoring-loki`/`monitoring-promtail` в репозитории
нет (полный обход дерева, включая `host-infra` с watchdog); единственное
оставшееся упоминание — annotation алерта `Watchdog Silent`, тоже
обновлён на `monitoring-alloy`.

### 4.6 Регрессии в существующих проверках

| Проверка | Результат |
|---|---|
| `loki -verify-config` (3.7.4, новый конфиг) | config is valid |
| `alloy validate` (v1.18.0, новый конфиг) | без замечаний |
| `docker compose -p monitoring config` | валиден, сервисы: loki, alloy, prometheus, grafana |
| YAML-lint всех конфигов (как в CI) | 8/8 OK |
| JSON-валидность дашбордов | OK |
| `scripts/check-docs-consistency.py` | см. §6 |
| `ci/compliance-report.sh` | см. §6 |
| `pytest tests/` | см. §6 |

---

## 5. Что сознательно не входило в область

**Grafana 12.0.2 → 13.1.1 и Prometheus v3.4.1 → v3.13.2 не тронуты.**
Задача — стек логирования; Grafana и Prometheus относятся к слою
визуализации и метрик. Мажорный апгрейд Grafana затрагивает провижининг
алертинга, который здесь хрупок и настраивался эмпирически (contact-point
рендерится через `sed`, а не `$VAR`, — типизация числового `chat_id`
ломает провижининг; проверено именно на 12.0.2). Смешивать это с миграцией
шиппера логов означает удваивать поверхность отката для продового канала
тревог. Выносится отдельной задачей.

**Сбор stdout-логов контейнеров не добавлялся.** Alloy это умеет
(`discovery.docker` + `loki.source.docker`), но это новый источник данных,
а не улучшение фильтрации: он меняет объём ingest и экономику retention.
Отдельное решение.

---

## 6. Прогон проверок репозитория

Результаты — в сообщении коммита и в выводе CI. Изменения затрагивают два
git-репозитория: `bks/monitoring` (вложенный, заигнорен родителем — сам
стек) и `nemohermes_bks` (документация). Деплой на прод выполняет
пайплайн `bks/monitoring` при push в `main`.
