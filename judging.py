"""The isolated judges receive different views. Only code supplies times and IDs."""
import itertools
from collections import Counter
from candidates import (
    DEFUSED_PAT,
    GENERIC,
    MID_ENTRY,
    SPOILER_PAT,
    Segment,
    conditional_stakes,
    cues,
    cut_id,
    dramatic_turn,
    ending,
    lookahead,
    opening,
    overlap,
    primary,
    signal,
    validate,
    validate_quotes,
)
from model import COLD, PAIR, RESOLUTION, SCORE, STITCH, boolean, string

TYPES = {"threat", "reveal", "relationship", "confrontation", "question", "visual", "other"}


def extract(show, parts):
    return [show[s.ep].inside(s.a, s.b) for s in parts]


def text_view(chunks, cold=False):
    speakers = {}
    result = []
    for beats in chunks:
        lines = []
        for b in beats:
            if b.label == "CAMERA" and any(x.label != "CAMERA" for x in beats):
                continue
            if cold and b.spoken:
                label = speakers.setdefault(b.label, f"Speaker {len(speakers)+1}")
            else:
                label = b.label
            lines.append(f"{label}: {b.text}")
        result.append(lines)
    return result


def semantic(client, show, parts):
    chunks = extract(show, parts)
    heads = opening(chunks[0], parts[0].a)
    tails = ending(chunks[-1])
    if not heads or not tails:
        raise ValueError("No certifiable opening or ending beat")
    judge = client.ask(
        "score",
        SCORE,
        {"promo": text_view(chunks), "opening": [b.text for b in heads], "ending": [b.text for b in tails]},
    )
    hook = string(judge.get("hook"), "quote")
    cliff = string(judge.get("cliffhanger"), "quote")
    for field in ["hook", "cliffhanger"]:
        string(judge[field], "why")
    validate_quotes(show, parts, hook, cliff)
    reason, kind = string(judge, "reason"), string(judge, "hook_type")
    scores = judge.get("scores")
    if (
        kind not in TYPES
        or not isinstance(scores, dict)
        or any(type(scores.get(k)) != int or not 0 <= scores[k] <= 5 for k in ("hook", "cliffhanger", "clarity"))
    ):
        raise ValueError("Invalid editorial score/type")
    if boolean(judge, "spoiler"):
        raise ValueError("Spoiler: " + reason)
    if min(scores[k] for k in ["hook", "cliffhanger", "clarity"]) < 3:
        raise ValueError("Weak editorial score: " + reason)
    cold = client.ask("cold", COLD, text_view(chunks, cold=True))
    for k in ("characters", "conflict", "next_question"):
        string(cold, k)
    if not boolean(cold, "clear"):
        raise ValueError("Cold viewer could not follow: " + cold["conflict"])
    after, covered = lookahead(show, parts[-1])
    if covered < 3000:
        raise ValueError("Fewer than 30 seconds of continuation in input")
    check = client.ask(
        "resolution", RESOLUTION, {"promo": text_view(chunks), "cliffhanger": cliff, "after_30_seconds": after}
    )
    why = string(check, "why")
    resolved, unfinished = boolean(check, "resolved"), boolean(check, "unfinished")
    if resolved or unfinished:
        raise ValueError("Resolved/unfinished: " + why)
    total = sum(scores[k] for k in ["hook", "cliffhanger", "clarity"])
    return dict(
        parts=parts,
        hook=judge["hook"],
        cliffhanger=judge["cliffhanger"],
        scores=scores,
        score=total,
        hook_type=kind,
        reason=reason,
        cold_viewer=cold,
        resolution=check,
        status="passed_model_checks",
    )


def baseline(show, option):
    points, parts, head, tail = option
    validate_quotes(show, parts, head.text, tail.text)
    kind = next(iter(cues(head.text)), "question" if "?" in head.text else "other" if head.spoken else "visual")
    chunks = extract(show, parts)
    beats = [b for chunk in chunks for b in primary(chunk)]
    if not any(b.spoken for b in beats) or len(head.text.split()) < 3 or GENERIC.match(head.text.strip()):
        raise ValueError("Opening too terse for an unchecked shortlist")
    if MID_ENTRY.search(head.text.strip()):
        raise ValueError("Mid-conversation opening depends on prior context")
    if not (cues(tail.text) or "?" in tail.text):
        raise ValueError("No explicit unresolved-stakes cue at the cut")
    if "?" in tail.text and len(tail.text.split()) <= 3:
        raise ValueError("Clarification question too short to hold a cliffhanger")
    if len(show) >= 10 and any(s.ep >= max(1, int(0.65 * len(show))) for s in parts):
        for b in beats:
            if SPOILER_PAT.search(b.text):
                raise ValueError("Spoiler: reveals late-series central identity twist")
    _, covered = lookahead(show, parts[-1])
    if covered < 3000:
        raise ValueError("Fewer than 30 seconds of continuation in input")
    last_ep = show[parts[-1].ep]
    next_spoken = [b for b in last_ep.inside(parts[-1].b, min(last_ep.end, parts[-1].b + 1500)) if b.spoken]
    if next_spoken and next_spoken[0].start <= parts[-1].b + 150 and next_spoken[0].label == tail.label:
        raise ValueError("Cut splits speaker's continuous monologue")
    early_after = " ".join(b.text for b in next_spoken)
    if DEFUSED_PAT.search(early_after):
        raise ValueError("False cliffhanger: tension defused or answered within 15s")

    speakers = [b.label for b in beats if b.spoken]
    distinct_speakers = list(dict.fromkeys(speakers))
    body = [b for b in beats if b.spoken and b not in (head, tail) and len(b.text.split()) >= 4]
    middle = max(body, key=signal) if body else None
    stitch_note = (
        f"2-segment stitch ({'cross-episode' if parts[0].ep != parts[-1].ep else 'intra-episode'}) compresses the setup and payoff; "
        if len(parts) > 1
        else ""
    )
    if middle:
        reason = (
            f'{stitch_note}{head.label} opens on a {kind} hook, escalates through {middle.label} (“{middle.text}”), '
            f'and cuts on {tail.label}’s unresolved cliffhanger'
        )
    else:
        reason = f"{stitch_note}{head.label} opens on a {kind} hook and escalates directly to {tail.label}’s closing turn"
    if conditional_stakes(tail.text):
        reason += " with an explicit life-or-death ultimatum."
    elif dramatic_turn(tail.text):
        reason += " at a high-stakes dramatic reversal."
    elif "?" in tail.text:
        reason += " leaving an unanswered confrontation question."
    else:
        reason += " with the consequence still hanging."
    return dict(
        parts=parts,
        hook={"quote": head.text, "why": f"Opening {kind} hook by {head.label} within 3s."},
        cliffhanger={
            "quote": tail.text,
            "why": f"Cuts on {tail.label}'s unresolved turn; verified against 15s post-cut defusal.",
        },
        score=points,
        scores=None,
        hook_type=kind,
        reason=reason,
        cold_viewer={
            "status": "heuristic_passed",
            "speakers_in_clip": distinct_speakers[:4],
            "self_contained_opening": True,
        },
        resolution={
            "status": "heuristic_passed",
            "available_after_seconds": covered / 100,
            "immediate_defusal_detected": False,
        },
        status="editor_review_required",
    )


def stitch_options(client, show, edges, first_choices, log):
    # Give the model legal pieces to combine, rather than asking it to invent timecodes.
    menu, summaries = {}, []
    for n, option in first_choices.items():
        _, (whole,), _, _ = option
        pts = sorted(edges[n])
        # Offer one opening and one ending fragment near 15s; avoid the entire original.
        interior = [p for p in pts if whole.a < p < whole.b]
        pieces = []
        for anchor in ("open", "close"):
            spans = [Segment(n, whole.a, p) if anchor == "open" else Segment(n, p, whole.b) for p in interior]
            spans = [s for s in spans if 800 <= s.b - s.a <= 2200]
            if spans:
                pieces.append(min(spans, key=lambda s: abs(s.b - s.a - 1500)))
        entries = []
        for s in pieces:
            bs = primary(show[n].inside(s.a, s.b))
            if bs:
                key = cut_id([s])
                menu[key] = s
                entries.append({"id": key, "seconds": (s.b - s.a) / 100, "head": bs[0].text, "tail": bs[-1].text})
        summaries.append({"ep": n, "summary": show[n].summary, "menu": entries})
    # One additional nonlocal batch lets an early setup meet a later payoff.
    batches = [summaries[i : i + 8] for i in range(0, len(summaries), 8)]
    if len(summaries) > 8:
        batches.append(summaries[:: max(2, (len(summaries) + 7) // 8)])
    for batch in batches:
        allowed = {x["id"] for row in batch for x in row["menu"]}
        try:
            proposals = client.ask("stitch", STITCH, batch).get("stitches")
            if not isinstance(proposals, list) or len(proposals) > 3:
                raise ValueError("Invalid stitch response")
        except ValueError as exc:
            log("stitch", "batch-" + str(batch[0]["ep"]), str(exc))
            continue
        for proposal in proposals:
            try:
                if (
                    not isinstance(proposal, list)
                    or not 2 <= len(proposal) <= 4
                    or any(not isinstance(x, str) or x not in allowed for x in proposal)
                ):
                    raise ValueError("Invalid stitch menu IDs or count")
                parts = tuple(menu[x] for x in proposal)
                validate(show, edges, parts)
                yield parts
            except ValueError as exc:
                log("stitch", str(proposal), str(exc))


def rank(rows, config, client, log):
    pool, counts = [], Counter()
    max_stitches = config.get("max_stitches", 2)
    stitch_count = 0
    # Prefer continuous cuts first when scores tie; keep stitches capped so continuous stretches remain the norm.
    for row in sorted(
        rows,
        key=lambda r: (
            -r["score"],
            len(r["parts"]) > 1,
            -sum(s.b - s.a for s in r["parts"]),
            cut_id(r["parts"]),
        ),
    ):
        parts = row["parts"]
        if any(overlap(parts, p["parts"]) for p in pool):
            log("rank", cut_id(parts), "Source footage overlaps an admitted candidate")
            continue
        if any(row["hook"]["quote"].casefold() == p["hook"]["quote"].casefold() for p in pool):
            log("rank", cut_id(parts), "Repeated opening quote")
            continue
        eps = {s.ep for s in parts}
        if any(counts[e] >= config["max_per_episode"] for e in eps):
            log("rank", cut_id(parts), "Episode cap")
            continue
        if len(parts) > 1 and stitch_count >= max_stitches:
            log("rank", cut_id(parts), "Stitch diversity cap")
            continue
        pool.append(row)
        counts.update(eps)
        if len(parts) > 1:
            stitch_count += 1
        if client and len(pool) >= config["pairwise_pool"]:
            break
    wins, matches = Counter(), []
    if client:
        for i, (a, b) in enumerate(itertools.combinations(pool, 2)):
            if i % 2:
                a, b = b, a

            def brief(r):
                return {k: r[k] for k in ("hook", "cliffhanger", "reason", "cold_viewer")}

            ids = [cut_id(r["parts"]) for r in (a, b)]
            try:
                w = client.ask("pair", PAIR, {"A": brief(a), "B": brief(b)}).get("winner")
                if w not in ("A", "B"):
                    raise ValueError("Invalid pairwise winner")
            except ValueError as exc:
                log("pair", " vs ".join(ids), str(exc))
                matches.append({"pair": ids, "winner": None})
                continue
            winner = ids[0 if w == "A" else 1]
            wins[winner] += 1
            matches.append({"pair": ids, "winner": winner})
        if any(m["winner"] is None for m in matches):
            raise RuntimeError(
                "Incomplete pairwise tournament. Model returned an invalid winner. Check the model/config before retrying."
            )
        pool.sort(key=lambda r: (-wins[cut_id(r["parts"])], -r["score"], cut_id(r["parts"])))
    picks, types = [], Counter()
    for row in pool:
        kind = row["hook_type"]
        if types[kind] >= config["max_per_hook_type"]:
            log("rank", cut_id(row["parts"]), "Hook-type diversity cap")
            continue
        types[kind] += 1
        row["pairwise_wins"] = wins[cut_id(row["parts"])] if client else None
        picks.append(row)
        if len(picks) == config["publish_limit"]:
            break
    return picks, matches
