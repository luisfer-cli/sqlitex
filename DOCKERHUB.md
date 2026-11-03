# SQLiteX

**Turn any SQLite database into a documented, queryable knowledge base — from your terminal.**

[![GitHub](https://img.shields.io/badge/source-GitHub-black?logo=github)](https://github.com/luisfer-cli/sqlitex)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/luisfer-cli/sqlitex/blob/main/LICENSE)

SQLiteX is a 100% CLI Python tool that inspects a SQLite database, documents its tables, columns, relationships, and semantics with an LLM, and then answers business questions in plain language. It retrieves relevant schema context (RAG), generates read-only SQL, executes it safely, and explains the result.

No dashboards. No notebooks. No vector database to operate. Just your `.sqlite` file, a model endpoint, and questions.

## Pull and run

```bash
docker pull luisfercli/sqlitex:latest

docker run --rm luisfercli/sqlitex:latest version
docker run --rm luisfercli/sqlitex:latest --help
```

The image runs as a non-root user and uses `/data` as its working volume. Catalogs and indexes are stored in `/data/.sqlitex`.

## Quick start

```bash
mkdir -p data

# 1. Create a sample e-commerce database
docker run --rm -v "$PWD/data:/data" \
  luisfercli/sqlitex:latest demo /data/demo.sqlite

# 2. Analyze it: schema, relationships, profiles, and AI annotations
docker run --rm -v "$PWD/data:/data" \
  -e SQLITEX_API_BASE_URL \
  -e SQLITEX_API_KEY \
  -e SQLITEX_MODEL \
  luisfercli/sqlitex:latest init /data/demo.sqlite

# 3. Ask a question
docker run --rm -v "$PWD/data:/data" \
  -e SQLITEX_API_BASE_URL \
  -e SQLITEX_API_KEY \
  -e SQLITEX_MODEL \
  luisfercli/sqlitex:latest ask "which products sold the most units?" \
  --db /data/demo.sqlite
```

`-e VAR` reads the value from your shell. For a file, use `--env-file` with literal values (Docker does not expand `${...}` there).

## What it does

- **Understands the database before answering.** `init` extracts schema, keys, indexes, triggers, samples, column profiles, and AI-generated semantics.
- **RAG over the schema.** Relevant tables, columns, meanings, and value distributions are retrieved before SQL generation.
- **Conversation and one-shot questions.** Use `ask` for a single question or `chat` for follow-ups with history.
- **Read-only by design.** `mode=ro`, `PRAGMA query_only`, an SQLite authorizer that denies writes/DDL/PRAGMA mutations, single-statement validation, timeouts, and row limits.
- **Self-correcting SQL.** If generated SQL fails, the error is sent back to the model for a repair attempt.
- **Any OpenAI-compatible endpoint.** OpenAI, OpenRouter, Ollama, LM Studio, vLLM, OpenCode Go, and similar.
- **Graceful fallback.** Without an embeddings endpoint, it uses BM25 lexical retrieval and keeps working.

## Commands

| Command | What it does |
| --- | --- |
| `init <db>` | Inspect the database, document it with AI, and build the RAG index. |
| `ask "<question>"` | Answer a question with RAG + generated SQL. |
| `chat` | Interactive conversation with history. |
| `schema [table]` | Show the catalog, a table, or an ER diagram. |
| `describe <table>` | Show columns, relationships, indexes, and sample rows. |
| `query "<sql>"` | Run SQL manually in strict read-only mode. |
| `reindex` | Rebuild the index. |
| `status` | List catalogs and their state. |
| `doctor` | Diagnose config, database, catalog, index, and API. |
| `config` | Show effective configuration without revealing the API key. |
| `demo [path]` | Generate a sample e-commerce database. |

## Configuration

Set these environment variables (or mount a `.env` file and run with `--env-file`):

```dotenv
SQLITEX_API_BASE_URL=https://api.openai.com/v1
SQLITEX_API_KEY=sk-...
SQLITEX_MODEL=gpt-4o-mini
# SQLITEX_EMBEDDINGS=auto
# SQLITEX_LANGUAGE=en
# SQLITEX_MAX_ROWS=50
```

Local Ollama:

```dotenv
SQLITEX_API_BASE_URL=http://host.docker.internal:11434/v1
SQLITEX_API_KEY=ollama
SQLITEX_MODEL=llama3.1
SQLITEX_EMBEDDINGS=off
```

> On Linux, add `--add-host=host.docker.internal:host-gateway` to reach a host-run Ollama.

See the full configuration reference in [`.env.example`](https://github.com/luisfer-cli/sqlitex/blob/main/.env.example).

## Safety

- Read-only SQLite connection with `query_only` and an authorizer that blocks writes, DDL, ATTACH, and mutating pragmas.
- Only `SELECT`, `WITH`, or `EXPLAIN` statements.
- Row limit and query timeout to avoid accidental full-table dumps.
- Sample rows are sent to the configured AI provider. Use a local endpoint or reduce exposure with `SQLITEX_SAMPLE_ROWS=0` and `--no-profile`.

## Tags and platform

- `latest`
- `0.1.0`
- `linux/amd64`

## Links

- **Source code:** https://github.com/luisfer-cli/sqlitex
- **Evaluation report:** https://github.com/luisfer-cli/sqlitex/blob/main/EVALUATION.md
- **Issues:** https://github.com/luisfer-cli/sqlitex/issues
- **License:** MIT
