"""Continuous windows, multi-segment stitches, source validation, and structural scoring."""
import re
from dataclasses import dataclass
from screenplay import stamp

CUES = {
    "threat": r"\b(kill|die|dead|death|hang|hanged|gallows|burn|flame|ash|blood|murder|poison|poisoning|poisoner|arrest|prison|dungeon|chains|execute|destroy|knife|sword|traitor|witch|witchcraft|briars|thorns|whip|strike)\w*\b",
    "reveal": r"\b(secret|lied|lying|stole|stolen|planted|property|forged|proof|evidence|pretend|betray|impostor|pregnant|confess|antidote|ring|locket|ledger|herbarium)\w*\b",
    "relationship": r"\b(bride|groom|marry|marriage|wedding|altar|wife|husband|sister|mother|father|son|daughter|hand|vow|disowned|inheritance|dowry|love|choose|chosen)\w*\b",
    "confrontation": r"\b(stop|leave|refuse|never|dare|shut|kneel|bow|owe|pay|price|orders|coward|guards|remove|crawl|beggar|shame|humiliat|cruel)\w*\b",
}
GENERIC = re.compile(r'^(what|why|no|yes|who|how|wait|you|really|please|mother|father|stop|leave)[?!. …]*$', re.I)
MID_ENTRY = re.compile(r'^(because|wherever|then|and|but|or|so|yes|no|oh|well|still)\b|^\.\.\.|^…', re.I)
DANGLING = re.compile(r'(?:\.\.\.|…|—|--)\s*$')
SPOILER_PAT = re.compile(
    r"\b(rightful (duke|duchess|heir|king|lord)|this is my son|you( are|'re) the (duke|duchess|heir)|"
    r"it was always me|alexander ashburn|duke of ashburn|duchess of ashburton|declared me dead|"
    r"loved the duke|make a duchess|duke's marriage)\b",
    re.I,
)
DEFUSED_PAT = re.compile(
    r"\b(one blow would (end|destroy)|as a scullery maid|if you seek shelter|earn it|here you go|they dirtied it)\b",
    re.I,
)


def cues(text):
    return [name for name, pat in CUES.items() if re.search(pat, text, re.I)]


def conditional_stakes(text):
    return bool(
        re.search(r"\b(if|unless|or else|refuse|refusal|otherwise|one flame|will hang her)\b", text, re.I)
        and "threat" in cues(text)
    )


def dramatic_turn(text):
    return bool(
        re.search(
            r"\b(accept my hand|admits she has been|never going to marry|should have chosen|guarded it better|there it is)\b",
            text,
            re.I,
        )
    )


def signal(beat):
    tags = cues(beat.text)
    words = len(beat.text.split())
    mid_pen = 4 if MID_ENTRY.search(beat.text.strip()) else 0
    return (
        2 * bool(tags)
        + min(2, len(tags))
        + ("?" in beat.text)
        + ("!" in beat.text)
        + (beat.spoken and 4 <= words <= 18)
        - 4 * bool(GENERIC.match(beat.text.strip()))
        - mid_pen
    )


def primary(beats):
    return [b for b in beats if b.label != "CAMERA"] or beats


def opening(beats, start):
    return [b for b in primary(beats) if b.end <= start + 300]


def ending(beats):
    # Evidence must come from a line/action actually present at the end of the cut.
    return [b for b in primary(beats) if b.end >= max(x.end for x in beats) - 100]


@dataclass(frozen=True)
class Segment:
    ep: int
    a: int
    b: int

    def row(self):
        return {"episode": self.ep, "start": stamp(self.a), "end": stamp(self.b)}


def cut_id(parts):
    return "+".join(f"E{s.ep:02}-{s.a}-{s.b}" for s in parts)


def validate(show, edges, parts):
    if not 1 <= len(parts) <= 4:
        raise ValueError("Expected 1 to 4 segments")
    if not 2000 <= sum(s.b - s.a for s in parts) <= 6000:
        raise ValueError("Outside 20–60 seconds")
    prev = None
    for s in parts:
        if s.ep not in show or s.a >= s.b or s.a not in edges[s.ep] or s.b not in edges[s.ep]:
            raise ValueError("Unknown boundary, reversed interval, or clipped source beat")
        if prev and (prev.ep > s.ep or (prev.ep == s.ep and prev.b >= s.a)):
            raise ValueError("Segments out of order, adjacent or overlapping")
        beats = show[s.ep].inside(s.a, s.b)
        if not beats or min(b.start for b in beats) != s.a or max(b.end for b in beats) != s.b:
            raise ValueError("Segment does not begin and end on a source beat")
        last_lines = [b for b in beats if b.spoken and b.end >= s.b - 100]
        if any(DANGLING.search(b.text) for b in last_lines):
            raise ValueError("Potential unfinished sentence at cut")
        prev = s


def validate_quotes(show, parts, hook, cliff):
    first, last = parts[0], parts[-1]
    for quote, pool, name in [
        (hook, opening(show[first.ep].inside(first.a, first.b), first.a), "hook"),
        (cliff, ending(show[last.ep].inside(last.a, last.b)), "cliffhanger"),
    ]:
        if not isinstance(quote, str) or not quote.strip() or not any(quote in b.text for b in pool):
            raise ValueError(f"Unverified {name} quote")


def overlap(left, right):
    return sum(max(0, min(a.b, b.b) - max(a.a, b.a)) for a in left for b in right if a.ep == b.ep)


def score_parts(show, parts):
    first, last = parts[0], parts[-1]
    chunks = [show[s.ep].inside(s.a, s.b) for s in parts]
    heads = [b for b in opening(chunks[0], first.a) if b.spoken] or opening(chunks[0], first.a)
    tails = [b for b in ending(chunks[-1]) if b.spoken] or ending(chunks[-1])
    if not heads or not tails:
        return None
    head = max(heads, key=signal)
    tail = max(tails, key=signal)
    body = [x for beats in chunks for x in beats if x.spoken]
    stakes = sum(bool(cues(x.text)) for x in body)
    end_bonus = (
        7
        if conditional_stakes(tail.text)
        else (5 if dramatic_turn(tail.text) else (3 if ("?" in tail.text and len(tail.text.split()) >= 5) else 0))
    )
    early_premise = 2 if first.ep <= 15 else 0
    stitch_bonus = 2 if len(parts) > 1 else 0
    score = (
        2 * signal(head)
        + signal(tail)
        + end_bonus
        + 2 * bool(set(cues(tail.text)) & {"threat", "reveal"})
        + min(stakes, 4)
        + early_premise
        + stitch_bonus
        + (len({x.label for x in body}) >= 2)
    )
    return (score, parts, head, tail)


def windows(show, ep, edges):
    options = []
    points = sorted(edges[ep.number])
    examined = 0
    for i, a in enumerate(points):
        for b in points[i + 1 :]:
            if b - a > 6000:
                break
            if b - a < 2000:
                continue
            examined += 1
            parts = (Segment(ep.number, a, b),)
            try:
                validate(show, edges, parts)
            except ValueError:
                continue
            scored = score_parts(show, parts)
            if scored:
                options.append(scored)
    options.sort(key=lambda x: (-x[0], -(x[1][0].b - x[1][0].a), x[1][0].a))
    return options, examined


def local_stitches(show, edges):
    """Propose 2-segment stitches when an episode opening and finale are >100s apart, or across consecutive episodes."""
    proposals = {}
    for n, ep in show.items():
        pts = sorted(edges[n])
        open_segs, close_segs = [], []
        for i, a in enumerate(pts):
            for b in pts[i + 1 :]:
                dur = b - a
                if 2000 <= dur <= 3000:
                    seg = Segment(n, a, b)
                    try:
                        validate(show, edges, (seg,))
                    except ValueError:
                        continue
                    beats = ep.inside(a, b)
                    hs = [x for x in opening(beats, a) if x.spoken]
                    ts = [x for x in ending(beats) if x.spoken]
                    if hs and a <= 1500:
                        h = max(hs, key=signal)
                        if signal(h) >= 5 and not MID_ENTRY.search(h.text.strip()) and len(h.text.split()) >= 3:
                            open_segs.append((signal(h), seg))
                    if ts and b >= ep.end - 3500:
                        t = max(ts, key=signal)
                        if signal(t) >= 5 or conditional_stakes(t.text) or dramatic_turn(t.text):
                            close_segs.append((signal(t) + 6 * conditional_stakes(t.text) + 4 * dramatic_turn(t.text), seg))
        open_segs.sort(key=lambda x: (-x[0], x[1].a))
        close_segs.sort(key=lambda x: (-x[0], x[1].b - x[1].a))
        for _, s1 in open_segs[:2]:
            for _, s2 in close_segs[:2]:
                if s1.b + 3000 <= s2.a and (s2.b - s1.a) >= 10000 and 4000 <= (s1.b - s1.a) + (s2.b - s2.a) <= 6000:
                    parts = (s1, s2)
                    try:
                        validate(show, edges, parts)
                        scored = score_parts(show, parts)
                        if scored:
                            proposals[parts] = scored
                            break
                    except ValueError:
                        continue

    for n in sorted(show):
        if n + 1 not in show:
            continue
        ep1, ep2 = show[n], show[n + 1]
        pts1, pts2 = sorted(edges[n]), sorted(edges[n + 1])
        s1_cands, s2_cands = [], []
        for i, a in enumerate(pts1):
            for b in pts1[i + 1 :]:
                if 2200 <= b - a <= 3000 and a >= ep1.end - 11000:
                    seg = Segment(n, a, b)
                    try:
                        validate(show, edges, (seg,))
                    except ValueError:
                        continue
                    hs = [x for x in opening(ep1.inside(a, b), a) if x.spoken]
                    if hs:
                        h = max(hs, key=signal)
                        if signal(h) >= 5 and not MID_ENTRY.search(h.text.strip()) and len(h.text.split()) >= 4:
                            s1_cands.append((signal(h), seg))
        for b in pts2[1:]:
            if 2200 <= b <= 3200:
                seg = Segment(n + 1, 0, b)
                try:
                    validate(show, edges, (seg,))
                    ts = [x for x in ending(ep2.inside(0, b)) if x.spoken]
                    if ts:
                        t = max(ts, key=signal)
                        if signal(t) >= 5 or conditional_stakes(t.text) or dramatic_turn(t.text):
                            s2_cands.append((signal(t) + 6 * conditional_stakes(t.text) + 4 * dramatic_turn(t.text), seg))
                except ValueError:
                    continue
        s1_cands.sort(key=lambda x: -x[0])
        s2_cands.sort(key=lambda x: -x[0])
        if s1_cands and s2_cands:
            parts = (s1_cands[0][1], s2_cands[0][1])
            try:
                validate(show, edges, parts)
                scored = score_parts(show, parts)
                if scored:
                    proposals[parts] = scored
            except ValueError:
                pass
    return proposals


def lookahead(show, last):
    """Use consecutive episode clocks. A truncated crossing track isn't fully observable."""
    remaining, epno, start, lines = 3000, last.ep, last.b, []
    while remaining and epno in show:
        ep = show[epno]
        stop = min(ep.end, start + remaining)
        for beat in ep.inside(start, stop):
            lines.append(f"{beat.label}: {beat.text}")
        remaining -= stop - start
        epno += 1
        start = 0
    return lines, 3000 - remaining
