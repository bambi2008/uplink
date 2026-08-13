import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "summarize_latency", ROOT / "scripts" / "summarize_latency.py"
)
SUMMARY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUMMARY)


class LatencySummaryTests(unittest.TestCase):
    def test_percentiles_and_nested_browser_stages(self):
        rows = [
            {"type": "chat_first_raw", "duration_ms": value}
            for value in (100, 200, 300, 400, 500)
        ]
        rows.append({
            "type": "turn_latency", "semantic_first_audio_ms": 900,
            "stages_ms": {"chat_first_speakable": 500, "semantic_audio_playing": 900},
            "transcript": "must never become a metric",
        })
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "latency.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot-json\n", encoding="utf-8")
            metrics, events, invalid = SUMMARY.load_metrics([path])
        result = SUMMARY.summarize(metrics)
        self.assertEqual(events, 6)
        self.assertEqual(invalid, 1)
        self.assertEqual(result["chat_first_raw.duration_ms"]["p50"], 300.0)
        self.assertEqual(result["chat_first_raw.duration_ms"]["p90"], 460.0)
        self.assertEqual(result["turn_latency.semantic_first_audio_ms"]["count"], 1)
        self.assertIn("turn_latency.stages.semantic_audio_playing_ms", result)
        self.assertFalse(any("transcript" in name for name in result))


if __name__ == "__main__":
    unittest.main()
