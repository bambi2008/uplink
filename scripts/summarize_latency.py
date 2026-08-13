#!/usr/bin/env python3
"""Summarize Uplink latency JSONL without exposing conversation content."""

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path


def percentile(values, quantile):
    """Return an interpolated percentile compatible with small local samples."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def load_metrics(paths):
    metrics = defaultdict(list)
    invalid = 0
    events = 0
    for path in paths:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (TypeError, json.JSONDecodeError):
                    invalid += 1
                    continue
                if not isinstance(event, dict):
                    invalid += 1
                    continue
                events += 1
                event_type = str(event.get("type") or "unknown")
                for key, value in event.items():
                    if key.endswith("_ms") and isinstance(value, (int, float)) and not isinstance(value, bool):
                        metrics[f"{event_type}.{key}"].append(float(value))
                stages = event.get("stages_ms")
                if isinstance(stages, dict):
                    for key, value in stages.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            metrics[f"{event_type}.stages.{key}_ms"].append(float(value))
    return metrics, events, invalid


def summarize(metrics):
    output = {}
    for name, values in sorted(metrics.items()):
        output[name] = {
            "count": len(values),
            "p50": round(percentile(values, 0.50), 1),
            "p90": round(percentile(values, 0.90), 1),
            "p95": round(percentile(values, 0.95), 1),
            "min": round(min(values), 1),
            "max": round(max(values), 1),
        }
    return output


def render_table(summary, events, invalid):
    lines = [
        f"events={events} invalid_lines={invalid}",
        "",
        "| metric | count | P50 ms | P90 ms | P95 ms | min ms | max ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, values in summary.items():
        lines.append(
            f"| {name} | {values['count']} | {values['p50']:.1f} | {values['p90']:.1f} | "
            f"{values['p95']:.1f} | {values['min']:.1f} | {values['max']:.1f} |"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Latency JSONL file(s)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)
    metrics, events, invalid = load_metrics(args.paths)
    result = summarize(metrics)
    if args.json:
        print(json.dumps({"events": events, "invalid_lines": invalid, "metrics": result}, indent=2))
    else:
        print(render_table(result, events, invalid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
