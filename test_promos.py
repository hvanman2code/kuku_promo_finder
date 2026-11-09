"""Unit tests for boundary validation, continuation/spoiler guards, local stitches, and LLM judge plumbing."""
import json
from pathlib import Path
import tempfile
from threading import Thread
from unittest.mock import Mock
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from candidates import Segment, lookahead, validate, validate_quotes
from evaluate import recovery
from judging import baseline, rank, semantic, stitch_options
from model import Client, COLD, PAIR, RESOLUTION, SCORE, STITCH
from screenplay import Beat, Episode, stamp, ticks

S = Segment(1, 0, 2200)


def episode(n):
    lines = [
        (0, 200, "Pay the ransom!"),
        (200, 1100, "They took the bus."),
        (1100, 2200, "Who has the key?"),
        (2200, 3200, "The phone rings."),
        (3200, 5200, "Nobody picks up."),
    ]
    return Episode(n, "Private logline unseen by cold reader", [Beat(a, b, "VOICE", t, "1") for a, b, t in lines], 5200)


class Checks(unittest.TestCase):
    def setUp(self):
        self.show = {1: episode(1), 2: episode(2)}
        self.edges = {n: ep.edges() for n, ep in self.show.items()}

    def test_boundaries_and_concurrent_tracks(self):
        validate(self.show, self.edges, [S])
        for parts in [
            [Segment(1, 0, 1999)],
            [Segment(1, 1, 2200)],
            [S, S],
            [Segment(2, 0, 1100), Segment(1, 0, 1100)],
            [Segment(1, 0, 5200), Segment(2, 0, 2200)],
        ]:
            with self.assertRaises(ValueError):
                validate(self.show, self.edges, parts)
        self.show[1].beats.append(Beat(2100, 2300, "CAMERA", "An unbroken shot.", "1"))
        self.assertNotIn(2200, self.show[1].edges())
        self.assertEqual(ticks(stamp(12503)), 12503)

    def test_quotes_and_lookahead(self):
        validate_quotes(self.show, [S], "Pay the ransom!", "Who has the key?")
        for hook, cliff in [("They took the bus.", "Who has the key?"), ("Pay the ransom!", "Nobody picks up.")]:
            with self.assertRaises(ValueError):
                validate_quotes(self.show, [S], hook, cliff)
        lines, covered = lookahead(self.show, Segment(1, 2200, 5200))
        self.assertIn("VOICE: Pay the ransom!", lines)
        self.assertNotIn("VOICE: The phone rings.", lines)
        self.assertEqual(covered, 3000)
        self.assertEqual(lookahead(self.show, Segment(2, 2200, 5200))[1], 0)

    def test_gold_one_to_one(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "labels.json"
            f.write_text(json.dumps({"promos": [{"id": str(i), "segments": [S.row()]} for i in range(2)]}))
            report = recovery(f, [{"parts": [S]}], self.show)
            self.assertEqual((report["recovered"], report["recall"]), (1, 0.5))

    def test_baseline_continuation_and_cold_start_guards(self):
        # 1. Mid-conversation opening should be rejected
        ep_mid = Episode(
            1,
            "Logline",
            [
                Beat(0, 200, "A", "Because no one should die hungry.", "1"),
                Beat(200, 1200, "B", "You stole the bread from the kitchen.", "1"),
                Beat(1200, 2200, "A", "Will you hang me from the gallows for a loaf?", "1"),
                Beat(2200, 5200, "B", "The guards wait outside.", "1"),
            ],
            5200,
        )
        show_mid = {1: ep_mid}
        opt_mid = (20, (S,), ep_mid.beats[0], ep_mid.beats[2])
        with self.assertRaisesRegex(ValueError, "Mid-conversation opening"):
            baseline(show_mid, opt_mid)

        # 2. Immediate conflict defusal within 15s should be rejected as a false cliffhanger
        ep_defused = Episode(
            1,
            "Logline",
            [
                Beat(0, 200, "A", "You dare show your face in this courtyard?", "1"),
                Beat(200, 1200, "B", "She tried to destroy you with poison!", "1"),
                Beat(1200, 2200, "A", "Will you cast her into the road to die?", "1"),
                Beat(2250, 3100, "B", "If you seek shelter in this house, earn it as a scullery maid.", "1"),
                Beat(3100, 5200, "A", "Take the apron.", "1"),
            ],
            5200,
        )
        show_def = {1: ep_defused}
        opt_def = (22, (S,), ep_defused.beats[0], ep_defused.beats[2])
        with self.assertRaisesRegex(ValueError, "False cliffhanger"):
            baseline(show_def, opt_def)

    def test_semantic_rejections(self):
        score = dict(
            hook={"quote": "Pay the ransom!", "why": "Demand"},
            cliffhanger={"quote": "Who has the key?", "why": "Question"},
            scores={"hook": 4, "cliffhanger": 4, "clarity": 4},
            hook_type="threat",
            reason="Direct threat",
            spoiler=False,
        )
        cold = dict(characters="Caller", conflict="Ransom", next_question="Where is the key?", clear=True)
        continuation = dict(why="The key is handed over.", resolved=True, unfinished=False)
        for replies, message in [
            ([dict(score, hook={"quote": "Invented line", "why": "Test"})], "Unverified hook"),
            ([dict(score, spoiler=True)], "Spoiler"),
            ([score, dict(cold, clear=False)], "Cold viewer"),
            ([score, cold, continuation], "Resolved/unfinished"),
            ([score, cold, dict(continuation, resolved=False, unfinished=True)], "Resolved/unfinished"),
        ]:
            client = Mock()
            client.ask.side_effect = replies
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                semantic(client, self.show, [S])

    def test_compatible_endpoint_and_cache(self):
        received = []

        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.server.assert_path(self.path, "/v1/chat/completions")
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompt, payload = (m["content"] for m in body["messages"])
                received.append((prompt, json.loads(payload)))
                answers = {
                    SCORE: dict(
                        hook={"quote": "Pay the ransom!", "why": "Demand."},
                        cliffhanger={"quote": "Who has the key?", "why": "Missing key."},
                        scores={"hook": 4, "cliffhanger": 4, "clarity": 4},
                        hook_type="threat",
                        reason="Concrete threat.",
                        spoiler=False,
                    ),
                    COLD: dict(
                        characters="Caller and driver",
                        conflict="Captive bus",
                        next_question="Who has the key?",
                        clear=True,
                    ),
                    RESOLUTION: dict(resolved=False, unfinished=False, why="There is only a ringing phone."),
                    STITCH: {"stitches": [["E01-0-1100", "E02-1100-2200"]]},
                    PAIR: {"winner": "B"},
                }
                envelope = {
                    "choices": [{"message": {"content": json.dumps(answers[prompt])}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(envelope).encode())

        server = HTTPServer(("127.0.0.1", 0), Endpoint)
        server.assert_path = self.assertEqual
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = dict(
                    provider="synthetic-test",
                    model="stub",
                    api_key_env="",
                    base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    max_per_episode=2,
                    max_stitches=2,
                    publish_limit=10,
                    pairwise_pool=15,
                    max_per_hook_type=3,
                )
                client = Client(cfg, Path(d) / "cache.sqlite")
                first = {n: (0, (Segment(n, 0, 2200),), None, None) for n in self.show}

                def fail(*args):
                    self.fail(str(args))

                stitches = list(stitch_options(client, self.show, self.edges, first, fail))
                self.assertEqual(stitches, [(Segment(1, 0, 1100), Segment(2, 1100, 2200))])
                result = semantic(client, self.show, stitches[0])
                self.assertEqual(result, semantic(client, self.show, stitches[0]))
                cold_payload = next(data for prompt, data in received if prompt == COLD)
                cold_text = json.dumps(cold_payload)
                self.assertNotIn("logline", cold_text)
                self.assertNotIn("VOICE", cold_text)
                self.assertNotIn("phone rings", cold_text)
                other = dict(result, parts=(Segment(2, 2200, 5200),), hook={"quote": "Second opening"})
                picks, matches = rank([result, other], cfg, client, fail)
                self.assertEqual(len(matches), 1)
                self.assertEqual(len(picks), 2)
                self.assertEqual(client.stats["cache_hits"], 3)
                client.db.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
