#!/usr/bin/env python3
"""Measure one real Codex run from its private rollout; emit only aggregate metadata."""
import argparse
import json
from datetime import datetime
from pathlib import Path


def seconds(timestamp):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


def summarize(records, until_call=None):
    start = end = None
    model = effort = endpoint = None
    calls = {}
    intervals = []
    responses = set()
    for event in records:
        kind, item = event["type"], event["payload"]
        subtype = item.get("type")
        timestamp = seconds(event["timestamp"])
        if kind == "event_msg" and subtype == "task_started":
            if start is not None:
                raise ValueError("more than one task before the selected result")
            start = timestamp
        if start is None:
            continue
        if kind == "turn_context":
            model, effort = item.get("model"), item.get("effort")
        elif kind == "token_usage_record" and item.get("response_id"):
            responses.add(item["response_id"])
        elif kind == "response_item":
            call = item.get("call_id")
            if subtype in {"function_call", "custom_tool_call"}:
                calls[call] = timestamp
            elif subtype in {"function_call_output", "custom_tool_call_output"} and call in calls:
                intervals.append((calls.pop(call), timestamp))
                output = str(item.get("output", ""))
                verdict = all(key in output for key in ("files_ok", "focus_ok", "actual_sha256"))
                matches_endpoint = call == until_call if until_call else verdict
                if matches_endpoint:
                    endpoint, end = call, timestamp
                    break
    if start is None or end is None:
        raise ValueError("missing task start or matching tool result")
    # Union on a single clock, clipping any concurrent tool still open at the end.
    intervals.extend((t, end) for t in calls.values())
    wall = end - start
    tool = 0.0
    right = start
    for left, stop in sorted(intervals):
        tool += max(0, stop - max(left, right))
        right = max(right, stop)
    return {"model_in_turn_context": model, "reasoning": effort, "endpoint_call_id": endpoint,
            "model_responses_through_result": len(responses) or None,
            "outer_tool_calls": len(intervals), "open_calls_at_endpoint": len(calls),
            "wall_s": round(wall, 3), "outer_tool_union_s": round(tool, 3),
            "outside_outer_tools_s": round(wall - tool, 3)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", type=Path, help="the individual benchmark model's local rollout")
    parser.add_argument("--until-call", help="explicit endpoint for a partial or diagnostic run")
    args = parser.parse_args()
    with args.rollout.open() as stream:
        print(json.dumps(summarize((json.loads(line) for line in stream), args.until_call)))
