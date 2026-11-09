# Editorial audit and failure log

These checks compare the tool's candidate cuts and rejection logs against the 50 supplied episode scripts.

## 1. Rejected false cliffhangers, mid-entry hooks, and spoilers (`outputs/failure_log.json`)

Upgrading the local baseline with a 15-second continuation lookahead, a mid-conversation cold-start filter, and a late-series spoiler guard automatically caught and rejected the failure modes that naive keyword scoring promoted:

| Candidate cut | Failure mode caught in code | Source verification |
|---|---|---|
| `E47 00:56.80–01:26.50` | **False cliffhanger (resolved in 12.7s)** | Cuts on Alexander's protest (“Juliana, she tried to destroy you!”). At `01:31.50` Juliana offers conditional shelter, and by `01:39.20` sentences Catherine to work as a scullery maid. |
| `E11 00:58.00–01:25.00` | **False cliffhanger (defused in 3s)** | Cuts on Gerald taunting Alexander to strike him. At `01:26.00–01:30.00` Alexander backs down (“One blow would end this. But one blow would destroy her life”) and the scene shifts to searching his coat. |
| `E42 00:23.40–01:12.50` & `E44/E45/E46` | **Late-series identity spoiler** | At `E42 01:04.20` Alexander's mother publicly names him the rightful Duke of Ashburn. Exposing the central 40-episode mystery spoils the show for cold top-of-funnel viewers. |
| `E02 00:00.00–00:51.50` | **Split speaker monologue** | Cuts on Clarissa's taunt (“Why are you surprised, sister? Look at you.”). Literally 0.2s later (`00:51.60`) Clarissa continues her own sentence (“That plain, old-fashioned gown.”). |
| `E03 00:08.50–00:43.30` | **Short clarification question** | Cuts on “Leave where?” (`00:42.10–00:43.30`), which is answered 0.1s later at `00:43.40` (“Wherever a discarded daughter belongs.”). |

## 2. Where the improved shortlist still fails or needs human review

Even with continuation and spoiler guards, a screenplay-only tool has honest limits that require either the live LLM judges or an editor watching source footage:

| Exported rank & cut | Remaining limitation | Editorial disposition |
|---|---|---|
| **Missed gold label** · `E41 00:41.00–01:33.50` | **Dialogue-free visual/audio cliffhanger.** At `01:21.80` the cathedral doors explode in a fireball (`ACTION` + `SOUND FX` only; zero spoken words in the final 14s). Because `ending()` requires a spoken or cue-bearing quote within 1s of the cut, the tool misses a top-tier visual cliffhanger (yielding 5/6 gold recall). | Relaxing the endpoint rule to allow high-intensity `ACTION`/`SOUND FX` beats when no dialogue is spoken would recover E41. |
| **Rank 7** · `E27 01:47.80–02:08.50` | **Abrupt cold-viewer entry.** Opening on “It is an antidote” (`20.7s`) lands immediately in a poisoning frame-up, but a first-time viewer does not see why a noblewoman is disguised as a chambermaid unless the cut starts earlier at `01:17.80` (`50.7s`). | Extend start point to `01:17.80` during editorial trim review. |
| **Rank 9** · `E05 00:49.00–01:36.50` | **Rhetorical insult vs. plot cliffhanger.** The cut includes Lady Ashcroft hurling Juliana's mother's ledger into the mud, but ends on Gerald's taunt (“A noblewoman reduced to a beggar's wife!”). The line has emotional sting, not an unresolved survival question. | Prefer Rank 1 (`E12`), Rank 4 (`E01+E02`), or Rank 5 (`E03`) for initial paid social testing. |
| **Ranks 4 & 5** · `E01+E02` and `E03` stitches | **Visual/music splice continuity.** Both 2-segment stitches join non-adjacent beats on valid block boundaries, but text timestamps cannot verify whether background music stems or lighting jump awkwardly across the cut. | Editor must preview the splice transition before export. |

## 3. Implementation mistake caught during development

The initial AI-drafted parser matched the screenplay header `Actions and dialogue:` with case-sensitive comparison and crashed on Episode 6 (`ACTIONS AND DIALOGUE:`). Switching to `.casefold()` recovered Episode 6 and let all 50 episodes parse cleanly.
