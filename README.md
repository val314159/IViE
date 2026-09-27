# IViE — (I)nteractive (Vi)sualization (E)ngine

IViE is a conversational analytics engine that translates natural-language questions into a constrained domain-specific language, executes deterministic analysis against real ERCOT grid data, generates visualizations, and returns grounded conversational answers.

The LLM interprets the question; the IViE engine owns the data and computation.

Loom: [https://www.loom.com/share/808eb369cdbe4aa7a281e8e0e77d4fe9]

## Quick Start

### 1. Create the Python environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start PostgreSQL

```bash
bash run_postgres.sh
```

Initialize and load the database:

```bash
bash bounce_db.sh
```

### 3. Start MeloYelo TTS

IViE expects the streaming TTS server at:

```text
ws://127.0.0.1:9009/audiows
```

### 4. Start IViE

```bash
.venv/bin/python app.py
```

Then open:

```text
http://localhost:8080/app/
```

## Tech Stack

- Python
- PostgreSQL 16
- OpenAI Codex app-server
- Matplotlib
- gevent / gevent-websocket
- Bottle
- Browser Web Speech API
- Web Audio API
- MeloYelo streaming TTS
- Tailwind CSS

## Architecture

```text
                     ┌──────────────────────┐
                     │      Browser UI      │
                     │ speech / text input  │
                     └──────────┬───────────┘
                                │ WebSocket
                                ▼
                     ┌──────────────────────┐
                     │        app.py        │
                     │ orchestration layer  │
                     └──────────┬───────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │   Codex app-server   │
                     │ natural language →   │
                     │ IViE DSL operation   │
                     └──────────┬───────────┘
                                │
                                ▼
                     ┌──────────────────────┐
                     │       plot.py        │
                     │ analytics DSL/engine │
                     └───────┬───────┬──────┘
                             │       │
                             ▼       ▼
                    PostgreSQL   PNG + JSON facts
                       ERCOT           │
                        data           ▼
                                ┌──────────────┐
                                │ Browser UI   │
                                │ chart + text │
                                │ + speech     │
                                └──────────────┘
```

`plot.py` acts as a domain-specific language for ERCOT analysis. Codex selects from supported analytical operations rather than inventing arbitrary SQL or numerical results.

Examples include:

```bash
./plot.py demand line
./plot.py demand compare ...
./plot.py outages monthly ...
./plot.py reserve lowest
./plot.py reserve compare ...
```

Each successful analysis produces:

```text
img/img_<timestamp>.png
img/img_<timestamp>.json
dun/img_<timestamp>.png
```

The PNG contains the visualization. The JSON sidecar contains structured facts derived from the query results. Codex reads those facts before producing its final conversational answer.

## Configuration

Example `.env`:

```bash
PORT=8080

CODEX_BIN=codex
# CODEX_MODEL=<optional model override>
CODEX_SANDBOX=danger-full-access

MELOYELO_URL=ws://127.0.0.1:9009/audiows

DATABASE_URL=postgresql://ivie_user:ivie_secret@localhost:5432/ivie_db
```

Codex must already be authenticated locally:

```bash
codex login
```

This project was developed and tested with OpenAI Codex CLI/app-server version `0.154.0`.

## Reproducing the Demo

Start PostgreSQL, MeloYelo, and IViE, then visit:

```text
http://localhost:8080/app/
```

Example supported questions:

```text
Show me electricity demand for August 2026.
Compare August 2026 demand with August 2025.
Compare this summer with last summer.
Show monthly demand for the last two years.

Show outages by month for the last year.
Were outages higher this summer than last summer?
Show the worst outage periods.

Show available capacity versus demand for August.
When did the grid have the least spare capacity?
Compare reserve margin this summer with last summer.
```

The application also includes an automated demo/regression mode that submits supported questions sequentially and records timing information.

## Dataset

The demo uses approximately two years of publicly available ERCOT Texas electric-grid data, covering September 2024 through August 2026.

The imported data includes:

- electricity demand
- electricity prices
- generation / fuel mix
- outages
- available capacity

Source data comes from publicly available ERCOT datasets and reports and is downloaded and transformed into a normalized PostgreSQL schema used by IViE.

No synthetic grid data is used in the primary demo.

## Grounding and Hallucination Control

IViE deliberately separates natural-language interpretation from analytical computation.

Codex translates a user's question into a constrained IViE analytical operation. The engine performs the database query and calculations, generates the visualization, and emits structured facts derived from the returned data.

Codex then explains those facts.

This architecture substantially reduces opportunities for fabricated numerical results because the language model is not responsible for generating the underlying data or calculations.

## Known Limitations

- The current implementation is specialized for the ERCOT domain.
- The analytical DSL currently exposes a finite set of supported analyses.
- Natural-language interpretation can select an inappropriate supported operation.
- Some price and generation/fuel-mix analysis paths are not reliable enough for the current demo.
- Browser speech recognition behavior depends on browser support.
- The current application is designed around a single active browser session.
- The current demo environment assumes locally running PostgreSQL, Codex, and MeloYelo services.

## Next Steps

- Expand the IViE DSL and supported analytical operations.
- Add additional datasets and domains beyond ERCOT.
- Improve natural-language routing and validation of selected operations.
- Add richer interactive visualizations.
- Package the data-ingestion and deployment process.
- Support multiple concurrent users and persistent sessions.
