"""Run one show. Stage all reports before replacing the files of a successful run."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
from candidates import cut_id, local_stitches, overlap, score_parts, validate, validate_quotes, windows
from evaluate import recovery
from judging import baseline, rank, semantic, stitch_options
from model import Client
from screenplay import read_show

ROOT = Path(__file__).resolve().parent


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_reports(out, picks, stats, failures, matches, quality):
    rows = []
    for i, item in enumerate(picks, 1):
        row = dict(item)
        parts = row.pop("parts")
        row.update(
            id=cut_id(parts),
            segments=[s.row() for s in parts],
            rank=i,
            seconds=sum(s.b - s.a for s in parts) / 100,
        )
        if i == 1:
            prefix = (
                "Post first: highest-scoring standalone ultimatum with zero backstory required —"
                if stats["mode"] == "baseline"
                else "Post first because"
            )
        elif len(parts) > 1:
            prefix = f"Rank #{i} (multi-segment stitch): test spliced pacing —"
        else:
            prefix = f"Rank #{i}: strong {row['hook_type']} angle —"
        row["post_first_because"] = prefix + " " + row["reason"]
        rows.append(row)
    dump(out / "promos.json", {"run": stats, "promos": rows})
    dump(out / "quality.json", quality)
    dump(out / "pairwise.json", {"mode": stats["mode"], "matches": matches})
    dump(out / "failure_log.json", failures)
    fields = [
        "rank",
        "id",
        "segment",
        "episode",
        "start",
        "end",
        "seconds_total",
        "hook",
        "cliffhanger",
        "why_it_works",
        "score",
        "hook_type",
        "post_first_because",
        "status",
    ]
    with (out / "promos.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            for j, s in enumerate(r["segments"], 1):
                writer.writerow(
                    dict(
                        rank=r["rank"],
                        id=r["id"],
                        segment=j,
                        **s,
                        seconds_total=r["seconds"],
                        hook=r["hook"]["quote"],
                        cliffhanger=r["cliffhanger"]["quote"],
                        why_it_works=r["reason"],
                        score=r["score"],
                        hook_type=r["hook_type"],
                        post_first_because=r["post_first_because"],
                        status=r["status"],
                    )
                )
    md = [
        "# Promo shortlist",
        "",
        f"{stats['episodes']} episodes · {stats['beats']:,} timed beats · {len(rows)} ranked cuts",
        "",
        (
            "**Local baseline with continuation & spoiler guards.** Scores combine hook signal, stakes, and cliffhanger force after rejecting mid-sentence splits, 15s false cliffhangers, and late-series identity spoilers."
            if stats["mode"] == "baseline"
            else "**Model-reviewed.** Scores are out of 15. Pairwise wins determine the order before diversity caps."
        ),
        "",
    ]
    if not rows:
        md += ["No candidates passed the configured gates.", ""]
    for r in rows:
        cuts = " + ".join(f"E{s['episode']:02} {s['start']}–{s['end']}" for s in r["segments"])
        kind_tag = f"{r['hook_type']} ({len(r['segments'])}-seg stitch)" if len(r["segments"]) > 1 else r["hook_type"]
        md += [
            f"## {r['rank']}. {cuts}",
            "",
            f"{r['seconds']:g}s · {kind_tag} · {r['score']} points",
            "",
            f"**Hook** “{r['hook']['quote']}”",
            "",
            f"**Cliffhanger** “{r['cliffhanger']['quote']}”",
            "",
            r["post_first_because"],
            "",
        ]
    (out / "promos.md").write_text("\n".join(md), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_dir", type=Path)
    p.add_argument("--config", type=Path, default=ROOT / "config.json")
    p.add_argument("--mode", choices=["auto", "model", "baseline"], default="auto")
    p.add_argument("--out", type=Path, default=ROOT / "outputs")
    p.add_argument("--cache", type=Path, default=ROOT / ".cache" / "responses.sqlite")
    p.add_argument("--gold", type=Path, default=ROOT / "gold.json")
    args = p.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    for key in ["windows_per_episode", "pairwise_pool", "publish_limit", "max_per_episode", "max_per_hook_type"]:
        if not isinstance(cfg[key], int) or cfg[key] < 1:
            p.error(f"Expected a positive integer for {key}")
    ready = not cfg["api_key_env"] or bool(os.getenv(cfg["api_key_env"]))
    mode = ("model" if ready else "baseline") if args.mode == "auto" else args.mode
    if mode == "model" and not ready:
        p.error(f"Set {cfg['api_key_env']} to run the model judges.")
    print("Mode:", mode, flush=True)
    show = read_show(args.input_dir)
    edges = {n: ep.edges() for n, ep in show.items()}
    client = Client(cfg, args.cache) if mode == "model" else None
    rows, failures, first, specs, options, episodes = [], [], {}, [], {}, []

    def log(stage, candidate, reason):
        failures.append(dict(stage=stage, candidate=candidate, reason=reason))

    for n, ep in show.items():
        pool, examined = windows(show, ep, edges)
        chosen = []
        for opt in pool:
            parts = opt[1]
            if not any(overlap(parts, old) for old in chosen):
                chosen.append(parts)
                specs.append(parts)
                options[parts] = opt
                first.setdefault(n, opt)
            if len(chosen) >= cfg["windows_per_episode"]:
                break
        episodes.append(
            dict(
                episode=n,
                beats=len(ep.beats),
                windows_examined=examined,
                eligible_windows=len(pool),
                shortlisted=len(chosen),
            )
        )
    for parts, opt in local_stitches(show, edges).items():
        specs.append(parts)
        options[parts] = opt
    if client:
        for parts in stitch_options(client, show, edges, first, log):
            specs.append(parts)
            if parts not in options:
                scored = score_parts(show, parts)
                if scored:
                    options[parts] = scored
    specs = list(dict.fromkeys(specs))
    for parts in specs:
        try:
            validate(show, edges, parts)
            r = semantic(client, show, parts) if client else baseline(show, options[parts])
            rows.append(r)
        except ValueError as exc:
            log("judge" if client else "baseline", cut_id(parts), str(exc))
    picks, matches = rank(rows, cfg, client, log)
    for i, r in enumerate(picks):
        validate(show, edges, r["parts"])
        validate_quotes(show, r["parts"], r["hook"]["quote"], r["cliffhanger"]["quote"])
        if any(overlap(r["parts"], x["parts"]) for x in picks[:i]):
            raise RuntimeError("Duplicate footage in export")
    stats = dict(
        mode=mode,
        model=cfg["model"] if client else None,
        provider=cfg["provider"] if client else None,
        episodes=len(show),
        beats=sum(len(e.beats) for e in show.values()),
        per_episode=episodes,
        windows_examined=sum(x["windows_examined"] for x in episodes),
        eligible_windows=sum(x["eligible_windows"] for x in episodes),
        candidates=len(specs),
        candidates_passing=len(rows),
        exported=len(picks),
        call_counts=dict(client.stats) if client else {},
        billed_usage_reported_this_run=dict(client.tokens) if client else {},
        input_fingerprint=hashlib.sha256(
            b"".join(sorted(hashlib.sha256(f.read_bytes()).digest() for f in args.input_dir.rglob("*.txt")))
        ).hexdigest(),
    )
    quality = recovery(args.gold, picks, show)
    args.out.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=args.out) as tmp:
        stage = Path(tmp)
        write_reports(stage, picks, stats, failures, matches, quality)
        for file in sorted(stage.iterdir()):
            file.replace(args.out / file.name)
    print(
        f"{len(show)} episodes | {stats['eligible_windows']} eligible windows | {len(specs)} shortlisted | {len(picks)} exported"
    )
    print(
        f"Gold recovery: {quality['recovered']}/{quality['gold_count']} (recall={quality['recall']:.2f})"
        if quality["gold_count"]
        else "Gold recovery: unmeasured (empty label file)"
    )
    print(args.out.resolve())


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"Run stopped: {exc}")
