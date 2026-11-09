"""Read the timestamped Kuku text format. All internal times are centiseconds."""
from dataclasses import dataclass
from pathlib import Path
import re

STAMP = r'\d+:[0-5]\d\.\d{2}'


def ticks(value):
    if not re.fullmatch(STAMP, value):
        raise ValueError(f"Invalid timestamp {value!r}")
    minutes, rest = value.split(":")
    return int(minutes) * 6000 + int(rest.replace(".", ""))


def stamp(t):
    return f"{t // 6000:02}:{t // 100 % 60:02}.{t % 100:02}"


@dataclass(frozen=True)
class Beat:
    start: int
    end: int
    label: str
    text: str
    scene: str

    @property
    def spoken(self):
        return self.label not in {"CAMERA", "ACTION", "SOUND FX"}


@dataclass
class Episode:
    number: int
    summary: str
    beats: list
    end: int

    def inside(self, start, end):
        return [b for b in self.beats if start <= b.start and b.end <= end]

    def edges(self):
        # A cut may never split ANY timed track, including a concurrent shot.
        points = {p for b in self.beats for p in (b.start, b.end)}
        return {p for p in points if not any(b.start < p < b.end for b in self.beats)}


def parse(path):
    raw = path.read_text(encoding="utf-8-sig")
    number = re.search(r"(?m)^EPISODE NUMBER:\s*Episode (\d+)\s*$", raw)
    if not number:
        raise ValueError("Missing episode number")
    summary = re.search(r"(?m)^EPISODE LOGLINE:\s*(.+)$", raw)
    body = raw.split("--- SCRIPT START ---")[1].split("--- SCRIPT END ---")
    if len(body) != 2:
        raise ValueError("Missing or repeated script end")
    beats, scene, scene_ends = [], "", []
    for paragraph in re.split(r"\n\s*\n", body[0].strip()):
        lines = [line.strip() for line in paragraph.strip().splitlines()]
        if lines[0].startswith("SCENE "):
            scene = lines[0]
            scene_ends += [ticks(line[11:]) for line in lines if line.casefold().startswith("scene end: ")]
        elif lines[0].casefold() == "actions and dialogue:":
            continue
        else:
            label = re.fullmatch(r"\*\*(.+?):\*\*", lines[0])
            times = re.fullmatch(rf"Start: \(({STAMP})\) \| End: \(({STAMP})\)", lines[1]) if len(lines) >= 3 else None
            if not (scene and times and label):
                raise ValueError(f"Malformed block in {scene}: {lines[0][:30]}")
            a, b = map(ticks, times.groups())
            if a >= b:
                raise ValueError(f"Invalid interval {a}, {b}")
            speaker = label[1].split("(")[0].strip()
            content = " ".join(lines[2:]).strip().strip('"“”')
            if not content:
                raise ValueError("Empty beat")
            beats.append(Beat(a, b, speaker, content, scene))
    if not beats or len(beats) != len(re.findall(r'(?m)^Start:', body[0])):
        raise ValueError("Timed-block count mismatch")
    return Episode(int(number[1]), summary[1] if summary else "", sorted(beats, key=lambda b: (b.start, b.end)),
                   max(scene_ends + [b.end for b in beats]))


def read_show(directory):
    show = {}
    for path in sorted(Path(directory).rglob("*.txt")):
        try:
            ep = parse(path)
        except (IndexError, ValueError) as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        if ep.number in show:
            raise ValueError(f"Episode {ep.number} appears twice. Supply one show per folder.")
        show[ep.number] = ep
    if not show:
        raise ValueError("No .txt screenplays found. Unzip the input pack first.")
    return dict(sorted(show.items()))
