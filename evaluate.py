"""One-to-one temporal IoU matching. A single hit cannot recover two gold promos."""
import json
from candidates import Segment, overlap
from screenplay import ticks


def recovery(path, ranked, show):
    rows = json.loads(path.read_text(encoding="utf-8"))["promos"]
    gold = []
    for row in rows:
        parts = [Segment(s["episode"], ticks(s["start"]), ticks(s["end"])) for s in row["segments"]]
        # A human may choose a frame between beats. Let that label count as a miss.
        if not 1 <= len(parts) <= 4 or not 2000 <= sum(s.b-s.a for s in parts) <= 6000:
            raise ValueError("Gold duration or segment count out of range")
        for i, s in enumerate(parts):
            if s.ep not in show or not 0 <= s.a < s.b <= show[s.ep].end:
                raise ValueError("Gold interval outside source")
            if i and (s.ep < parts[i-1].ep or (s.ep == parts[i-1].ep and s.a < parts[i-1].b)):
                raise ValueError("Gold segments out of order or overlapping")
        if not row["id"] or row["id"] in [g[0] for g in gold]:
            raise ValueError("Gold IDs must be unique and nonempty")
        gold.append((row["id"], parts))
    links = []
    for _, parts in gold:
        options = []
        for i, r in enumerate(ranked):
            pred = r["parts"]
            shared = overlap(parts, pred)
            union = sum(s.b-s.a for s in [*parts, *pred]) - shared
            if shared / union >= .5:
                options.append((shared / union, i))
        links.append(sorted(options, reverse=True))
    matched = {}

    def assign(g, seen):
        for iou, i in links[g]:
            if i in seen:
                continue
            seen.add(i)
            if i not in matched or assign(matched[i][0], seen):
                matched[i] = (g, iou)
                return True
        return False

    for g in range(len(gold)):
        assign(g, set())
    hits = [{"gold_id": gold[g][0], "rank": i+1, "iou": round(v, 3)} for i,(g,v) in sorted(matched.items())]
    return {"gold_count": len(gold), "recovered": len(hits) if gold else None, "ranked_count": len(ranked),
            "recall": len(hits) / len(gold) if gold else None, "iou_threshold": .5, "matches": hits}
