# knowledge-graph

Локальный code intelligence для arenadata-фронтенда (`adsw`, `network`).
Type-aware индексация на ts-morph, граф в Neo4j, REST API на FastAPI для
MCP-сервера и Web UI.

## Стек

| Компонент | Где | Порт |
|---|---|---|
| Neo4j Community (граф + Browser) | Docker | 7687 / 7474 |
| ts-morph indexer (Fastify, Node) | локально | 7401 |
| FastAPI core | локально | 7400 |
| MCP-обёртка | `arenadata-mcp` | — |

## Быстрый старт

```bash
cp .env.example .env          # настроить PROJECT_<NAME>
uv run python -m kg.cli install
./start.sh                    # docker compose up -d Neo4j
uv run python -m kg.cli indexer    # в отдельном терминале
uv run python -m kg.cli api        # в отдельном терминале
uv run python -m kg.cli reindex adsw
```

После старта:
- Neo4j Browser: <http://localhost:7474> (login: значения из `NEO4J_USER` / `NEO4J_PASSWORD` в `.env`)
- API health:   <http://127.0.0.1:7400/health>
- API docs:     <http://127.0.0.1:7400/docs>

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

## Фазы

- **Phase 0** — скелет: Neo4j up, indexer/core skeletons, MCP `kg_health`
- **Phase 1** — GraphQL operations + hooks + callsites
- **Phase 2** — Routes resolve (route → component → guards → gql)
- **Phase 3** — Pages, Permissions (source-of-truth), Docs (`CLAUDE/`)
- **Phase 4** — E2E specs + page-objects + testid bridge
- **Phase 5** — SCSS modules

## Безопасность

- `.env` в `.gitignore` — все секреты (пароль Neo4j, пути к проектам) живут только там
- `docker-compose.yml` берёт `NEO4J_USER` / `NEO4J_PASSWORD` из `.env`; **обязательно**
  поставить непустое значение перед первым `docker compose up`
- Пути к индексируемым кодовым базам задаются переменными `PROJECT_<NAME>=…` в `.env`
  и не попадают в репозиторий
