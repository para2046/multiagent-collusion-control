"""Post-hoc A/B replay of a recorded episode through the ControlArena monitor (via our adapter),
across visibility configs. Reuses ControlArena score parsing; needs a monitor model."""
import argparse, asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multiagent_harness.events import read_events
from multiagent_harness.ca_adapter import score_events_offline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--model", default="openai/openai/gpt-4.1-mini")
    ap.add_argument("--configs", nargs="+", default=["M0", "M2", "M4"])
    args = ap.parse_args()
    ev = read_events(args.events)

    async def run():
        out = []
        for c in args.configs:
            r = await score_events_offline(ev, c, args.model)
            out.append(r)
            print(f'{c}: suspicion={r["suspicion_score"]:.3f}  msgs={r["n_messages"]}  reason={r["reason"][:90]}')
        return out

    res = asyncio.run(run())
    Path(args.events).with_name("ca_replay.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
