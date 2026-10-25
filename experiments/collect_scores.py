"""Scan all single-agent transcripts under the SHADE-Arena task folders and print a
score table (agent, task, main, side, monitor suspicion). Pure stdlib."""
import json, re, sys
from pathlib import Path

SA = Path(__file__).resolve().parents[1] / "environments" / "shade_arena"
rows = []
for task in ("bank_transfer_2", "api_key_calendar_agendas_2"):
    base = SA / "task_pairs" / task
    for sub in ("transcripts_no_secondary_task", "transcripts", "transcripts_no_hint"):
        for f in (base / sub).rglob("*.json"):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            if "conversation" not in d:
                continue
            parts = f.relative_to(base / sub).parts
            agent = "/".join(parts[:-3]) if len(parts) >= 3 else parts[0]
            m = re.search(r"<verdict>([\d.]+)</verdict>", d.get("monitoring_response") or "")
            rows.append((agent, task, sub, str(d.get("main_task_evaluation"))[:48],
                         str(d.get("side_task_evaluation")), m.group(1) if m else "-", len(d["conversation"])))
rows.sort()
print(f"{'agent':40} {'task':28} {'variant':30} {'main':50} {'side':6} {'susp':5} msgs")
for r in rows:
    print(f"{r[0]:40} {r[1]:28} {r[2]:30} {r[3]:50} {r[4]:6} {r[5]:5} {r[6]}")
