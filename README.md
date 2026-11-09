# Find the Promos

Find source timecodes for 20–60 second microdrama ads across a multi-episode screenplay pack. Point the CLI at an extracted show folder: it parses timed dialogue, action, sound, and camera blocks, filters out mid-sentence cuts, 15-second false cliffhangers, and late-series plot spoilers, and exports a ranked shortlist of continuous cuts and multi-segment stitches.

## Run

Requires Python 3.10+ (standard library only; zero external packages). Unzip the episode pack into a directory outside the repo and run:

```sh
python run.py "/path/to/extracted/show"
```

With no API key set, `run.py` runs the **local structural + continuation-checked pipeline** (`--mode baseline`), which produced all files in `outputs/` across the 50 episodes in ~1.5 seconds. When an API key is configured, the same command runs the four isolated LLM judges (`--mode model`).

```sh
# macOS / Linux (optional LLM judge mode)
export GROQ_API_KEY="your-key"
python run.py "/path/to/extracted/show" --mode model --out outputs_model
```

```powershell
# Windows PowerShell (optional LLM judge mode)
$env:GROQ_API_KEY = "your-key"
python run.py "C:\private\screenplays" --mode model --out outputs_model
```

## Outputs

| File | Contents |
|---|---|
| `outputs/promos.md` | Ranked top-10 promo shortlist with episode timecodes, hooks, cliffhangers, and posting-order rationale |
| `outputs/promos.csv` | Flat segment table (one row per segment; group by `id` in `segment` order for multi-segment stitches) |
| `outputs/promos.json` | Full manifest with run statistics, per-episode window counts, quotes, cold-viewer/resolution flags, and scores |
| `outputs/failure_log.json` | Audit log of rejected candidates (false cliffhangers, late-series spoilers, split monologues, diversity caps) |
| `outputs/quality.json` | Temporal IoU (`>= 0.50`) recall and matched ranks against the 6 benchmark promos in `gold.json` (`5/6` recovered) |
| `outputs/pairwise.json` | Pairwise tournament log (populated in `--mode model`) |
| `review_notes.md` | Honest audit of caught failures plus remaining blind spots (e.g., E41's dialogue-free explosion cliffhanger) |
| `solution.pdf` / `solution.md` | Two-page assignment solution document |

## What ran on the 50-episode pack

Across **50 episodes** (`3,727` timed blocks), the tool evaluated **5,215** legal boundary windows, shortlisted **98** candidates (95 continuous windows + 3 candidate multi-segment stitches), passed **61** through the cold-start, continuation, and anti-spoiler gates, and exported **10 ranked promos** spanning 9 episodes:

- **8 continuous cuts** (led by Rank #1 `E12 00:20.80–01:09.50`, 48.7s, where a planted pocket watch turns into a bare-handed blackthorn briar ultimatum or Juliana's husband hangs).
- **2 multi-segment stitches** (Rank #4 `E01 00:35.40–01:00.10 + E02 00:00.00–00:30.60`, a 55.3s cross-episode bridge uniting the cathedral altar betrayal with the groom's public rejection; and Rank #5 `E03 00:08.50–00:30.30 + E03 01:59.20–02:20.50`, a 43.1s intra-episode stitch compressing a 132s span from the stolen wedding confrontation to the scarred stranger's surprise ring proposal).
- **5/6 gold benchmark recovery (`83.3%` recall)** at temporal IoU `>= 0.50` against `gold.json`, with the single miss (`E41 00:41.00–01:33.50`, where the cathedral doors explode in a fireball with zero spoken dialogue in the final 14s) highlighting an honest boundary of dialogue-anchored cliffhanger rules.

## Architecture: Code vs. Pluggable AI Judges

1. **Deterministic timing & boundary integrity (`screenplay.py`, `candidates.py`)**: Converts `MM:SS.cc` timestamps into integer centiseconds. Any cut that bisects a concurrent `CAMERA`, `ACTION`, `SOUND FX`, or dialogue block is rejected.
2. **Cold-start, 15s continuation & spoiler guards (`candidates.py`, `judging.py`)**:
   - Rejects hooks that open on discourse continuations (`"Because..."`, `"Wherever..."`) or terse interjections.
   - Inspects the 30-second continuation across episode boundaries (`lookahead()`): rejects cuts that split a speaker's continuous monologue (e.g., `E02 00:51.50`), end on 2-word clarification questions (`E03 00:43.30` `"Leave where?"`), or defuse tension within 15 seconds (`E47 01:26.50` scullery maid offer; `E11 01:25.00` aborted punch).
   - Blocks late-series identity reveals (`E42–E46` Duke/Duchess unmasking) so top-of-funnel promos never spoil the 40-episode mystery.
3. **Principled stitching (`local_stitches`, `stitch_options`)**: Never inserts a jump cut when a setup and cliffhanger already fit inside a single `<= 60s` window. Stitches are proposed only when an episode's opening hook and closing cliffhanger sit `>= 100s` apart (such as `E03`) or bridge consecutive episodes (`E01 + E02`), and are capped via `max_stitches` in `config.json`.
4. **Swappable OpenAI-compatible LLM judges (`model.py`)**: Changing `provider`, `base_url`, `model`, and `api_key_env` in `config.json` connects any provider (Groq, OpenAI, Gemini, local vLLM/Ollama) with SQLite content-addressed caching and rate-limit retries.

## Tests

```sh
python -m unittest -v
```
