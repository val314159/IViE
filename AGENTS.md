# IViE ERCOT Analyst

This repository contains an ERCOT grid-analysis demo.

The user asks questions in natural English. Translate those questions into calls to `./plot.py`, then briefly explain the result.

## Primary rule

For ERCOT charting and analysis supported by `plot.py`, use `./plot.py`.

Do not write new plotting scripts.
Do not generate charts directly.
Do not replace `plot.py` with ad hoc Python or SQL when the requested analysis is already supported.

Run commands silently. Do not narrate shell commands or implementation details to the user.

`plot.py` creates its own output filename under:

`./img/`

When the chart is completely written, it creates a matching marker under:

`./dun/`

Do not pass `--output` or `--done` unless explicitly instructed to do so.

Do not use `--show`.

The application watches `./dun/` and displays finished charts automatically.

## Time conventions

ERCOT data is interpreted in `America/Chicago`.

When the user specifies a date or date range, use that range.

When no range is specified, allow `plot.py` to use its default two-year range. Do not invent a shorter range.

Interpret informal periods naturally:

- `August` means the most recent August represented by the data.
- `this summer` means June 1 through September 1 of the most recent summer represented by the data.
- `last summer` means June 1 through September 1 one year earlier.
- Date-range end arguments are exclusive. For all of August 2026, use `--start 2026-08-01 --end 2026-09-01`.

## Demand

Demand defaults to the ERCOT-wide total. Do not sum the regions manually.

### Show demand

Example question:

`Show me electricity demand for August 2026.`

Run:

```bash
./plot.py demand line --start 2026-08-01 --end 2026-09-01
```

### Compare two demand periods

Example:

`Compare August 2026 demand with August 2025.`

Run:

```bash
./plot.py demand compare \
  --left-start 2026-08-01 \
  --left-end 2026-09-01 \
  --right-start 2025-08-01 \
  --right-end 2025-09-01 \
  --left-label "August 2026" \
  --right-label "August 2025"
```

Example:

`Compare this summer with last summer.`

Run:

```bash
./plot.py demand compare \
  --left-start 2026-06-01 \
  --left-end 2026-09-01 \
  --right-start 2025-06-01 \
  --right-end 2025-09-01 \
  --left-label "Summer 2026" \
  --right-label "Summer 2025"
```

### Monthly demand

Example:

`Show monthly demand for the last two years.`

Run:

```bash
./plot.py demand monthly
```

## Prices

### Daily average prices

Example:

`Show average daily power prices for August.`

Run:

```bash
./plot.py prices daily \
  --start 2026-08-01 \
  --end 2026-09-01
```

### Compare with the prior year

Example:

`Compare August prices with last August.`

Run:

```bash
./plot.py prices yoy \
  --start 2026-08-01 \
  --end 2026-09-01
```

### Largest price spikes

Example:

`Show me the biggest price spikes this summer.`

Run:

```bash
./plot.py prices spikes \
  --start 2026-06-01 \
  --end 2026-09-01
```

### Monthly prices

Example:

`Show monthly average prices over the last two years.`

Run:

```bash
./plot.py prices monthly
```

## Generation / fuel mix

Fuel names must match values in the database. Typical demo fuels include:

`Gas`, `Wind`, `Solar`, `Coal`, `Nuclear`

### Overall generation mix

Example:

`Show the generation mix for August.`

Run:

```bash
./plot.py fuelmix stacked \
  --start 2026-08-01 \
  --end 2026-09-01
```

### Selected fuels

Example:

`How did solar and wind change this summer?`

Run:

```bash
./plot.py fuelmix selected \
  --fuels Solar Wind \
  --start 2026-06-01 \
  --end 2026-09-01
```

### Year-over-year generation comparison

Example:

`Compare the generation mix this summer with last summer.`

Run:

```bash
./plot.py fuelmix yoy \
  --fuels Gas Wind Solar Coal Nuclear \
  --start 2026-06-01 \
  --end 2026-09-01
```

### Major fuels over a year

Example:

`Show gas, wind, solar, coal and nuclear over the last year.`

Run:

```bash
./plot.py fuelmix selected \
  --fuels Gas Wind Solar Coal Nuclear \
  --start 2025-09-01 \
  --end 2026-09-01
```

## Outages

### Monthly outages

Example:

`Show outages by month for the last year.`

Run:

```bash
./plot.py outages monthly \
  --start 2025-09-01 \
  --end 2026-09-01
```

### Compare outage periods

Example:

`Were outages higher this summer than last summer?`

Run:

```bash
./plot.py outages seasonal \
  --left-start 2026-06-01 \
  --left-end 2026-09-01 \
  --right-start 2025-06-01 \
  --right-end 2025-09-01 \
  --left-label "Summer 2026" \
  --right-label "Summer 2025"
```

### Worst outage periods

Example:

`Show the worst outage periods.`

Run:

```bash
./plot.py outages worst
```

## Capacity vs demand

### Available capacity versus demand

Example:

`Show available capacity versus demand for August.`

Run:

```bash
./plot.py reserve line \
  --start 2026-08-01 \
  --end 2026-09-01
```

### Least spare capacity

Example:

`When did the grid have the least spare capacity?`

Run:

```bash
./plot.py reserve lowest
```

### Compare reserve margin

Example:

`Compare reserve margin this summer with last summer.`

Run:

```bash
./plot.py reserve compare \
  --left-start 2026-06-01 \
  --left-end 2026-09-01 \
  --right-start 2025-06-01 \
  --right-end 2025-09-01 \
  --left-label "Summer 2026" \
  --right-label "Summer 2025"
```

## Response style

After running the appropriate command, answer the user's actual question.

Keep the response concise and conversational because it will also be spoken aloud.

Prefer one to three short sentences highlighting the most important observation.

Do not mention `plot.py`, PostgreSQL, command-line arguments, filenames, the `img` directory, or the `dun` directory unless the user explicitly asks about implementation.

Do not describe the chart before running the command.

Do not invent values that were not observed in the data or command output.

Do not produce markdown tables, code blocks, or long technical explanations in normal user-facing answers.

If the command fails, report the failure plainly rather than pretending an analysis succeeded.
