# knowledge-graph

Локальный граф вашего фронтенд-проекта. Берёт исходники и достаёт оттуда
то, что обычно приходится копать grep'ом по пять минут — какой компонент
рендерит этот роут, какие GraphQL-операции внутри хука, какие e2e-тесты
сломаются, если убрать data-testid. Складывает всё в Neo4j и отдаёт AI-
ассистенту через MCP.

## Зачем оно

Допустим, вы открываете незнакомую страницу `/services/education/booking`.
Чтобы понять, что там происходит, надо:

1. Найти этот path в `router/index.tsx`
2. Открыть лениво подключённый компонент `Bookings`
3. Посмотреть, какой хук он зовёт — `useBookings`
4. Открыть хук, найти GraphQL-операцию `GET_EDUCATION_BOOKINGS`
5. Заглянуть в схему этой операции
6. Проверить, какие e2e-тесты её пробивают
7. Посмотреть, какие permissions её защищают

Семь файлов, семь grep'ов, пятнадцать минут. С графом — один tool-call:

```
routes_resolve(/services/education/booking)
→ guards:      RouterGuard
→ permissions: education.booking.read  (ADMIN, EDUCATION_HEAD, EDUCATION_MANAGER)
→ components:  Bookings @ pages/education/booking/Bookings.tsx:103
→ hooks:       useBookings
→ operations:  [query] GET_EDUCATION_BOOKINGS (GetEducationBookings)
```

И таких «один call вместо семи grep'ов» там штук пятнадцать.

## Что внутри

```
┌──────────────────────────────────────────────────────────────┐
│  Neo4j 5 Community (Docker)                                  │
│    :7474 — Browser, на нём можно гулять по графу мышью       │
│    :7687 — Bolt (для API)                                    │
│                                                               │
│  Node + ts-morph (Fastify, :7401)                             │
│    держит ваш TS-проект в памяти, отдаёт type-aware AST       │
│                                                               │
│  Python + FastAPI (:7400)                                     │
│    REST-API и оркестратор извлечений                          │
│                                                               │
│  MCP-обёртка для Claude Code / Cursor / любого AI             │
│    тонкий HTTP-клиент → API                                   │
└──────────────────────────────────────────────────────────────┘
```

Анализ — **type-aware**: используется тот же TypeScript Compiler API, что
и в IDE. Это не regex по тексту, а настоящий `Find References`.

## Что лежит в графе

После полной индексации adsw (≈55k LOC, 985 source files, ~7 секунд):

| Узлы          | Сколько | Что это                                              |
|---------------|--------:|-------------------------------------------------------|
| GqlOperation  |     202 | каждая query/mutation/subscription/fragment           |
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

И сверху — 2.6k рёбер, которые всё это связывают (`RENDERS`, `WRAPS`,
`USES_OPERATION`, `CALLS_HOOK`, `GUARDED_BY`, `REQUIRES`, `BELONGS_TO`,
`USES_POM`, `RESOLVES_TO`, ...).

## Что можно делать

**Через MCP (в Claude Code / Cursor):**

```
gql_find_callsites GET_EDUCATION_BOOKING
  — где используется эта query, во всех компонентах и хуках

routes_resolve /services/education/booking
  — полная карта роута: компоненты, guards, permissions, gql

permissions_info education.booking.read
  — какие роли, какие роуты её требуют, где определена

pages_get education/booking
  — все компоненты страницы, файлы по ролям, gql, роуты

e2e_testid_info page-action-add
  — где в коде, какие локаторы, какие POM, какие спеки

e2e_coverage
  — сколько testid'ов покрыто e2e
```

**Через Neo4j Browser** (открывается на `localhost:7474`):

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

## Установка

Что должно быть в системе:
- Python 3.12+
- Node 20+ и `pnpm`
- Docker (для Neo4j)
- `uv` (`brew install uv` или `curl -LsSf https://astral.sh/uv/install.sh | sh`)

Шаги:

```bash
git clone <repo> knowledge-graph
cd knowledge-graph

cp .env.example .env
# открыть .env, заполнить:
#   NEO4J_PASSWORD=<любой непустой>
#   PROJECT_ADSW=/абсолютный/путь/к/packages/adsw

uv run python -m kg.cli install   # ставит pnpm + python зависимости
./start.sh                         # интерактивное меню
```

Меню выглядит так:

```
╭────────── knowledge-graph ──────────╮
│  Neo4j     ● running                │
│  Indexer   ● running                │
│  Core API  ● running                │
│  Projects  adsw                     │
╰─────────────────────────────────────╯

› What now?
❯ Start all
  Stop all
  Reindex project
  Tail logs
  Open Neo4j Browser
  Quit
```

Выбираете `Start all`, ждёте секунд 20, потом `Reindex project` — через
7 секунд граф наполнен.

## Подключить к Claude Code (или другому MCP-клиенту)

Этот репо не содержит MCP-сервера сам по себе — он отдаёт REST API. MCP-
обёртку (тонкий HTTP-клиент) надо положить в свой MCP-сервер. Пример
такой обёртки на FastMCP лежит в комментариях к `kg/api/*.py`.

В двух словах: новый MCP-tool делает `httpx.get(f"{api}/routes/resolve",
...)` и форматирует ответ. На уровне Claude это превращается в команды
типа `routes_resolve`, `gql_find_callsites` и т.п.

## Под капотом

Полная индексация = пять экстракторов, запускаются по очереди:

1. **gql** — Node + ts-morph бежит по `src/gql/queries/**/*.ts`, находит
   tagged-template literals \`\`gql\`...\`\`\`, парсит их через graphql-js,
   достаёт operations + fragments. Потом по `src/gql/hooks/` находит
   хуки-обёртки, через `findReferences()` собирает все вызовы.

2. **routes** — парсит `router/index.tsx` как обычный объект-литерал,
   разворачивает JSX `element:` (отдельно guards, отдельно Lazy-wrappers,
   отдельно сам компонент), резолвит `React.lazy(() => import(...))` до
   реального файла.

3. **permissions** — обходит `common/permissions/index.ts` как
   ObjectLiteralExpression, собирает все leaf-узлы вида
   `{create: ['ADMIN', ...], read: [...]}` в dot-keyed permission'ы.

4. **pages** — простой обход файловой системы по конвенции
   `pages/<domain>/<entity>/{Entity,Entities,types,helpers,constants,...}`.

5. **e2e** — regex по `e2e/tests/*.spec.ts` и `e2e/pages/*`, попутно
   собирает все `data-testid` строки и из e2e-локаторов, и из adsw .tsx.
   Связывает по значению.

Всё это пишется в Neo4j через Cypher батчами, поэтому быстро. Reindex
идемпотентен — старые узлы и рёбра проекта удаляются перед записью
новых.

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

## Что внутри проекта

```
knowledge-graph/
├── docker-compose.yml        # Neo4j
├── .env.example              # шаблон конфига
│
├── indexer/                  # Node + ts-morph (Fastify :7401)
│   └── src/
│       ├── server.ts
│       ├── project.ts        # ProjectRegistry, держит ts-morph Project
│       └── extractors/
│           ├── gql.ts
│           ├── routes.ts
│           └── permissions.ts
│
├── kg/                       # Python (FastAPI :7400)
│   ├── server.py
│   ├── settings.py           # читает .env
│   ├── db.py                 # Neo4j driver
│   ├── cli.py                # typer CLI
│   ├── api/                  # роутеры FastAPI
│   └── extractors/           # оркестрируют indexer + пишут в Neo4j
│
└── start.py                  # интерактивное меню (questionary + rich)
```

## Безопасность

`.env` — в `.gitignore`. Туда же идут пароль Neo4j и пути к вашему коду.
Без `.env` ничего не стартует: и docker-compose, и Python API падают с
понятной ошибкой, если `NEO4J_PASSWORD` пустой. В трекаемых файлах нет
ни хардкод-паролей, ни персональных путей — репозиторий безопасно
публиковать.
