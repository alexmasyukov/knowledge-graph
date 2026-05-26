# knowledge-graph

Локальный code intelligence для arenadata-фронтенда (`adsw`, `network`).
Type-aware индексация на ts-morph, граф в Neo4j, REST API на FastAPI для
MCP-сервера и Web UI.

## Зачем оно

Открываете незнакомую страницу `/services/education/booking`. Чтобы понять,
что там происходит, обычно надо:

1. Найти этот path в `router/index.tsx`
2. Открыть лениво подключённый компонент `Bookings`
3. Посмотреть, какой хук он зовёт — `useBookings`
4. Открыть хук, найти GraphQL-операцию `GET_EDUCATION_BOOKINGS`
5. Заглянуть в её схему
6. Проверить, какие e2e-тесты её пробивают
7. Посмотреть, какие permissions её защищают

Семь файлов, семь grep'ов. С графом — один tool-call:

```
routes_resolve /services/education/booking
→ guards:      RouterGuard
→ permissions: education.booking.read  (ADMIN, EDUCATION_HEAD, EDUCATION_MANAGER)
→ components:  Bookings @ pages/education/booking/Bookings.tsx:103
→ hooks:       useBookings
→ operations:  [query] GET_EDUCATION_BOOKINGS (GetEducationBookings)
```

Анализ — **type-aware**: используется тот же TypeScript Compiler API, что
и в IDE. Это не regex по тексту, а настоящий `Find References`.

## Стек

| Компонент | Где | Порт |
|---|---|---|
| Neo4j Community (граф + Browser) | Docker | 7687 / 7474 |
| ts-morph indexer (Fastify, Node) | локально | 7401 |
| FastAPI core | локально | 7400 |
| MCP-обёртка | `arenadata-mcp` | — |

## Быстрый старт

Нужно: Python 3.12+, Node 20+, `pnpm`, Docker, `uv`.

```bash
cp .env.example .env          # NEO4J_PASSWORD, PROJECT_<NAME>
uv run python -m kg.cli install
./start.sh                    # интерактивное меню (questionary + rich)
```

После старта:
- Neo4j Browser: <http://localhost:7474> (login: значения из `NEO4J_USER` / `NEO4J_PASSWORD` в `.env`)
- API health:   <http://127.0.0.1:7400/health>
- API docs:     <http://127.0.0.1:7400/docs>

## Что лежит в графе

После полной индексации adsw (≈55k LOC, 985 source files, ~7 секунд):

| Узлы          | Сколько | Что это                                              |
|---------------|--------:|-------------------------------------------------------|
| GqlOperation  |     202 | query/mutation/subscription/fragment                  |
| GqlHook       |      93 | хуки-обёртки из `src/gql/hooks/`                      |
| Route         |     101 | роуты из React Router                                 |
| Component     |      62 | компоненты, упомянутые в роутере                      |
| Page          |      50 | страницы по конвенции `pages/<domain>/<entity>/`      |
| Permission    |     135 | ключи из `PERMISSIONS` + найденные в JSX              |
| E2eSpec       |      21 | Playwright-спеки                                      |
| PageObject    |      19 | POM-классы из `e2e/pages/`                            |
| TestId        |     383 | все `data-testid` в коде                              |
| TestIdLoc     |     225 | локаторы из `*.locators.ts`                           |
| ScssModule    |      22 | `*.module.scss`                                       |

Плюс 2.6k рёбер: `RENDERS`, `WRAPS`, `USES_OPERATION`, `CALLS_HOOK`,
`GUARDED_BY`, `REQUIRES`, `BELONGS_TO`, `USES_POM`, `RESOLVES_TO`, ...

## Что можно делать

Через MCP (в Claude Code / Cursor):

```
gql_find_callsites GET_EDUCATION_BOOKING   — где используется операция
routes_resolve /services/education/booking — полная карта роута
permissions_info education.booking.read    — роли + роуты, требующие ключ
pages_get education/booking                — всё про страницу
e2e_testid_info page-action-add            — код → локатор → POM → спек
e2e_coverage                               — покрытие testid'ов e2e
scss_class_usage container                 — где объявлен и используется
```

Через Neo4j Browser:

```cypher
// какие компоненты падут, если изменить мутацию updateBooking
MATCH (op:GqlOperation {gql_name: 'UpdateBooking'})
      <-[:WRAPS]-(h:GqlHook)
      <-[:CALLS_HOOK]-(c:Component)
RETURN c, h, op

// какие e2e-тесты зависят от data-testid 'page-action-add'
MATCH (t:TestId {value: 'page-action-add'})
      <-[:RESOLVES_TO]-(:TestIdLoc)
      <-[:DEFINES_LOC]-(po:PageObject)
      <-[:USES_POM]-(spec:E2eSpec)
RETURN spec.name, spec.file

// топ-10 самых используемых GraphQL-хуков
MATCH (c:Component)-[:CALLS_HOOK]->(h:GqlHook)
RETURN h.name, count(DISTINCT c) AS users
ORDER BY users DESC LIMIT 10
```

## Под капотом

Полная индексация — пять экстракторов, по очереди:

1. **gql** — Node + ts-morph бежит по `src/gql/queries/**/*.ts`, находит
   tagged-template literals `` gql`...` ``, парсит через graphql-js,
   достаёт operations + fragments. Потом по `src/gql/hooks/` находит
   хуки-обёртки, через `findReferences()` собирает все вызовы.

2. **routes** — парсит `router/index.tsx` как объект-литерал,
   разворачивает JSX `element:` (guards, Lazy-wrappers, сам компонент),
   резолвит `React.lazy(() => import(...))` до реального файла.

3. **permissions** — обходит `common/permissions/index.ts` как
   ObjectLiteralExpression, собирает leaf'ы вида
   `{create: ['ADMIN', ...], read: [...]}` в dot-keyed permission'ы.

4. **pages** — обход файловой системы по конвенции
   `pages/<domain>/<entity>/{Entity,Entities,types,helpers,constants,...}`.

5. **e2e** — regex по `e2e/tests/*.spec.ts` и `e2e/pages/*`. Собирает
   `data-testid` строки и из e2e-локаторов, и из adsw `.tsx`. Связывает
   по значению.

Всё пишется в Neo4j через Cypher батчами. Reindex идемпотентен — старые
узлы и рёбра проекта удаляются перед записью новых.

## Под какой стек заточено

Сейчас — под Arenadata-фронтенды (adsw, network):
- React + TypeScript
- Apollo Client + GraphQL
- React Router v6
- Playwright e2e с POM-конвенцией
- `data-testid` повсюду

Если у вас другой стек, экстракторы (особенно routes и pages) надо
подгонять под ваши конвенции. Часть, которая ts-morph + GraphQL —
довольно generic.

## Структура

```
knowledge-graph/
├── docker-compose.yml      # Neo4j
├── pyproject.toml          # python deps (uv)
├── indexer/                # ts-morph extractor (Fastify :7401)
│   └── src/
└── kg/                     # FastAPI core (:7400)
    ├── server.py
    ├── api/
    ├── extractors/
    └── cli.py
```

## Архитектурные решения

Короткие записки о неочевидном выборе технологий — в `docs/adr/`.
Каждая отвечает на один вопрос «почему X, а не альтернативы».

- [0001 — Neo4j, не Postgres](docs/adr/0001-neo4j-as-the-graph-store.md)
- [0002 — ts-morph, не SCIP/tree-sitter](docs/adr/0002-ts-morph-instead-of-scip-or-tree-sitter.md)
- [0003 — Node sidecar по HTTP, не stdio](docs/adr/0003-node-sidecar-over-http.md)
- [0004 — REST, не GraphQL](docs/adr/0004-rest-not-graphql.md)
- [0005 — Один граф, проекты тегаются](docs/adr/0005-multi-project-single-repo.md)
- [0006 — Порядок экстракторов](docs/adr/0006-gql-extractor-runs-first.md)

## Безопасность

- `.env` в `.gitignore` — все секреты (пароль Neo4j, пути к проектам) живут только там
- `docker-compose.yml` берёт `NEO4J_USER` / `NEO4J_PASSWORD` из `.env`; **обязательно**
  поставить непустое значение перед первым `docker compose up`
- Пути к индексируемым кодовым базам задаются переменными `PROJECT_<NAME>=…` в `.env`
  и не попадают в репозиторий
