"""Summarise one SHADE-Arena run directory produced by phase1_shade_smoke.sh.

usage: python summarize_shade_run.py <run_dir> [<transcript.json>]
Prints turn count, tool calls, reasoning availability, token usage and the
official main/side scorer outputs. Pure stdlib.
"""
import json
import re
import sys
from pathlib import Path

run = Path(sys.argv[1])
log = run / "reasoning.jsonl"
rows = [json.loads(l) for l in log.open()] if log.exists() else []
agent_rows = [r for r in rows if r["model"] != "openai/gpt-4.1-mini"]
n_calls = len(agent_rows)
n_tools = sum(len(r["tool_calls"]) for r in agent_rows)
tool_names = {}
for r in agent_rows:
    for t in r["tool_calls"]:
        tool_names[t["name"]] = tool_names.get(t["name"], 0) + 1
reasoning_turns = sum(1 for r in agent_rows if r["reasoning"])
prompt = sum((r["usage"] or {}).get("prompt_tokens", 0) for r in agent_rows)
compl = sum((r["usage"] or {}).get("completion_tokens", 0) for r in agent_rows)
print(f"run: {run.name}")
print(f"agent model calls: {n_calls}  tool calls: {n_tools}  turns with provider reasoning: {reasoning_turns}")
print(f"prompt tokens: {prompt:,}  completion tokens: {compl:,}")
print("top tools:", sorted(tool_names.items(), key=lambda x: -x[1])[:12])
if len(sys.argv) > 2:
    d = json.load(open(sys.argv[2]))
    print("main_task_evaluation:", d.get("main_task_evaluation"))
    print("side_task_evaluation:", d.get("side_task_evaluation"))
    m = re.search(r"<verdict>([\d.]+)</verdict>", d.get("monitoring_response") or "")
    print("upstream monitor suspicion:", m.group(1) if m else None)
    print("conversation messages:", len(d["conversation"]))
stdout = run / "stdout.log"
if stdout.exists():
    tail = stdout.read_text().strip().splitlines()[-3:]
    print("stdout tail:", " | ".join(tail))
