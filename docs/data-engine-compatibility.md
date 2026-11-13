# Data SQL engine compatibility

The visual query adapters target PostgreSQL 12+, MySQL 8.0.17+ and MariaDB 10.6+.
SQLite's minimum is 3.30. These are SQL feature minimums. Each network source still
requires its own verified TLS connection and restricted read-only account; the
engine catalogue continues to report network qualification as pending.

MySQL's minimum is 8.0.17 because exact text comparisons use the NO PAD
`utf8mb4_0900_bin` collation, which Oracle added in that release ([release notes](https://dev.mysql.com/doc/relnotes/mysql/8.0/en/news-8-0-17.html)).
MariaDB Community 10.6 does not have a native UUID type. Its UUID reflection/filter
test runs on 10.7+; the remaining conformance suite runs on 10.6 too
([MariaDB UUID reference](https://mariadb.com/docs/server/reference/data-types/string-data-types/uuid-data-type)).

## Reproduce the version matrix

Run Docker locally, then from the repository:

```sh
uv run --locked --extra development --extra media python scripts/test_data_engine_matrix.py --output-dir /tmp/termx-engine-evidence
```

Use `--only pg12`, for example, to select one target. The matrix contains:

| Target | Image | Required server version |
| --- | --- | --- |
| pg12 | postgres:12 | 12.x |
| pg16 | postgres:16 | 16.x |
| mysql8017 | mysql:8.0.17 | Exactly 8.0.17 |
| mysql84 | mysql:8.4 | 8.4.x |
| maria106 | mariadb:10.6 | 10.6.x |
| maria1011 | mariadb:10.11 | 10.11.x |
| maria118 | mariadb:11.8 | 11.8.x |

Each target starts its own disposable container, publishes an ephemeral port only
on `127.0.0.1`, verifies the actual server family and version, and removes that
container and its data afterwards. The MySQL 8.0.17 image uses amd64; Apple Silicon
Docker Desktop runs that row under emulation. Tests use temporary TermX profiles
and unique synthetic schemas/databases. No existing database or desktop profile
is used.

Each row runs the dialect, engine review, conformance and engine parity modules.
They execute the shared query corpus against an independent Python oracle,
schema reflection and relationships, date buckets over multiple leap/ISO week
years, exact text comparisons, result JSON types and server-side deadlines.
Tests for the other two network engines are explicitly skipped in each row.
MySQL has no native UUID test; MariaDB Community 10.6 skips that one feature too.
When the pivot/segment, phase-two review and view-based report test modules are
present, the runner adds them and requires their selected live checks to pass too.

`summary.json` and each target's `result.json` record the server version, Docker
image ID/digest, architecture, source commit and content hash, command, totals,
timestamps and cleanup result. A row fails if a required selected-engine live
check is missing, skipped or fails, even when pytest otherwise exits successfully.
`pytest.xml`, `pytest.log` and `server.log` retain the underlying evidence. Image
tags for version lines can advance to later patch releases; the evidence states
which exact release ran. Passing this matrix does not claim coverage of every
intermediate server release, all character sets, production data, or the full
TLS/account enrollment flow.

The separate **Data engine compatibility** GitHub workflow runs the same seven
targets on changes to the Data backend, tests, dependency lock or matrix runner.
It uploads evidence even when a target fails. Local skips without configured live
URLs do not replace this gate.
