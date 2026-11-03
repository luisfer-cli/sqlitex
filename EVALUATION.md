# SQLiteX end-to-end evaluation

Evaluation run on **2026-11-03** with `deepseek-v4.1-flash` on OpenCode Go, using lexical retrieval (BM25; the provider does not expose embeddings) and six deterministic synthetic SQLite databases.

The goal was not just to check that the CLI starts, but to verify whether answers match **SQL-computed ground truth** and whether AI annotations describe each database accurately.

## Method

- Each database is generated with synthetic data and realistic relationships.
- Every question has a reference SQL query that computes the correct answer on the same database.
- A real `init` + `ask` run is executed.
- We check:
  - **SQL OK**: the generated query ran without errors and returned rows.
  - **Data OK**: the result matches the reference truth (with tolerance for money/averages).
  - **Answer OK**: the final text contains the key figure or name (not just plausible wording).
- Cases with ties or ambiguous business interpretations were rephrased so the question has a single correct answer.

## Test databases

| Database | Contents | Objects | Questions |
| --- | --- | ---: | ---: |
| E-commerce | customers, orders, line items, products, categories, reviews, employees, sales view | 9 | 3 |
| Human Resources | departments, employees with hierarchy, projects, assignments, payroll, absences | 6 | 4 |
| IoT sensors | sites, devices, 6,000 readings, alerts, maintenance | 5 | 4 |
| Online academy | teachers, courses, students, enrollments, exams, attendance | 6 | 5 |
| Irregular schema | names with spaces, composite PK, **no FKs**, FTS5 table | 5 | 5 |
| Mini library | books and sales | 2 | 3 |
| **Total** | | **33** | **24** |

## Results

| Metric | Result |
| --- | ---: |
| Objects annotated by AI | **33/33** |
| Global summaries generated | **6/6** |
| Warnings during `init` | **0** |
| SQL executed successfully | **24/24** |
| Result matches SQL ground truth | **24/24** |
| Text answer contains the key fact | **24/24** |
| Queries solved on the first attempt (no repair) | **24/24** |
| Average `ask` latency | **3.5 s** |
| Average `init` time | **3.5 s per object** |
| Total `init` time for all six databases | **104.4 s** |

Breakdown:

| Database | Annotated | Relationships | Init | Questions OK |
| --- | ---: | ---: | ---: | ---: |
| E-commerce | 9/9 | 8 | 29.3 s | 3/3 |
| Human Resources | 6/6 | 7 | 19.8 s | 4/4 |
| IoT sensors | 5/5 | 4 | 13.8 s | 4/4 |
| Online academy | 6/6 | 5 | 14.7 s | 5/5 |
| Irregular schema | 5/5 | 1 (1 inferred) | 14.6 s | 5/5 |
| Mini library | 2/2 | 1 | 12.2 s | 3/3 |

The full report with generated SQL, expected/actual rows, and answers is written to:

- `eval-results/report.md`
- `eval-results/report.json`

## What worked well

1. **Filters and semantic conditions.** The salary question required `active = 1`; the model applied it correctly and was not misled by two high salaries from inactive employees.
2. **Dates.** Explicit ISO ranges (`2026-10-01` to `2026-10-31`) and yearly aggregates (`2025`, `2026`) were handled correctly with `strftime`/comparisons.
3. **Joins.** Academy, IoT, HR, and commerce questions required joins across 2–4 tables; all worked.
4. **Views.** The revenue question used base tables in one run and the `product_sales_summary` view in another; both paths were valid.
5. **Hostile schemas.** Column names with spaces (`"Full Name"`), the `"orders_2026"` table, a composite PK in `order_lines`, and a total absence of foreign keys did not block the answers. The model inferred joins from semantics.
6. **FTS5.** The wireless-products question resolved against the virtual table; the system accepts these queries after the fix described below.
7. **No invented figures.** In all 24 cases, numbers mentioned in the answer appeared in the SQL result. No numeric hallucinations were detected.
8. **First attempt.** All 24 queries ran without needing automatic SQL repair.

## Issues found and fixes

### 1. Real bug: FTS5 blocked by the authorizer (fixed)

The first run failed on the FTS question with:

```text
Error executing SQL: authorization denied
```

Cause: FTS5 internally runs `PRAGMA data_version` during a read. SQLiteX's SQLite *authorizer* denied **all** pragmas, including read-only ones.

Fix in `src/sqlitex/query.py`:

- allow a whitelist of read-only pragmas (`data_version`, `table_info`, `index_list`, etc.);
- still deny any pragma with an assigned value (`PRAGMA x = y`), which could change state;
- add regression test `test_fts_queries_are_allowed`.

After the fix, FTS queries and `LIKE` on virtual tables work inside read-only mode.

### 2. Ambiguity in "best-selling product" (evaluation case)

In the first English run, the model excluded cancelled orders when counting units sold, which is a reasonable business interpretation, while the reference included them.

The question now explicitly says **"in non-cancelled orders"**, and the reference query applies the same filter. This tests instruction following instead of an arbitrary preference.

### 3. Ties in "best average rating" (evaluation case)

Two products had exactly the same average rating and review count. The model picked one and the reference picked another; both were correct.

The question now asks **"which products share the best average rating"** and is validated as a set, so tie handling is tested rather than a specific winner.

### 4. Test data adjustments

- The academy had no students averaging below 5; low-performing cases were added so the question is not trivial.
- The irregular-schema database originally had a tie for best-selling product; one winner was guaranteed.
- A deliberate tie in reviews remains to test tie handling.

## Limitations observed

- **No embeddings with this provider.** RAG worked in lexical mode (BM25). Vector retrieval should help with very heterogeneous vocabulary or paraphrased questions.
- **Broad context on small databases.** With 9 objects, relationship expansion includes almost the entire schema (7–8 tables per query). On databases with 50–200 tables, context budget and top-k precision need validation.
- **Conservative relationship inference.** In the irregular schema, only `orders_2026.customer_id → Customers` was inferred. `order_lines.order_id → orders_2026` was not, because the table name has a year suffix. The model compensated for the missing FK, but the catalog could help more.
- **Answer formatting.** Minor issues: sometimes uses a decimal point (`8.3`) instead of a comma, includes internal IDs, or adds redundant caveats ("only one record was returned").
- **Sensitivity to wording.** Ambiguous questions produce valid but different answers. In real use, ask with explicit filters.
- **`init` cost and latency.** One AI call per table; ~3.5 s per object with 3 workers. A 100-table database can take several minutes and use many tokens.
- **Not tested:** very large databases, concurrent writes, or embeddings-enabled models in this batch.

## Recommendations

1. Configure an embeddings endpoint and run `reindex` to compare hybrid vs lexical on the same question bank.
2. Improve FK inference: ignore year suffixes (`orders_2026` → `orders`) and try semantic matching with model help.
3. Normalize numeric output according to `SQLITEX_LANGUAGE` and avoid internal IDs in answers.
4. Add a 30–50 table dataset to measure retrieval precision, context size, and cost.
5. Add adversarial questions: misleading column names, NULL-heavy columns, mixed date formats, division by zero, and empty aggregates.
6. For production, cache annotations by schema hash (already implemented) and consider an incremental `init` for new tables.

## Reproduce

```bash
# Requires a .env with a configured AI provider
uv run python scripts/eval_live.py --rebuild
uv run python scripts/eval_live.py --only edge_schema --no-reuse --verbose
```

The script creates databases under `eval-results/data/`, stores catalogs and indexes under `eval-results/home/`, and writes `report.md` + `report.json`. A full run consumes tokens from the configured provider.
