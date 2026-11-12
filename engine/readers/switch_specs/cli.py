"""
CLI for the switch spec agent.

Natural language (default - uses the answer() router):
    python cli.py "Cisco Catalyst 9300-48P"
    python cli.py "compare C9300-48P vs EX4400-48P"
    python cli.py "which switches support 400G"
    python cli.py "switches with PoE over 600W"
    python cli.py                       # interactive REPL

Structured flags (still supported):
    python cli.py --vendors
    python cli.py --vendor Cisco --list
    python cli.py --filter --vendor Arista --min-speed 100
"""
import argparse
import json
import sys
import time

from agent import SpecAgent


def render(agent: SpecAgent, resp: dict) -> None:
    t = resp["type"]
    print()
    if t == "vendors":
        print(resp["message"])
        for vendor, count in resp["vendors"]:
            print(f"  {vendor:20s} {count}")
        return
    if t in ("empty", "notfound"):
        print(resp["message"])
        if resp.get("suggestions"):
            print("\nKnown models include:")
            for s in resp["suggestions"]:
                print(f"  - {s}")
        return
    if t == "spec":
        print(resp["message"])
        print()
        print(agent.format_spec(resp["result"]))
        if resp.get("source") == "live":
            fc = resp.get("field_confidence") or {}
            if fc:
                avg = sum(fc.values()) / len(fc)
                print(f"\n  (live web result · {len(fc)} fields · "
                      f"avg confidence {avg:.0%} · cached for next time)")
        if resp.get("alternates"):
            print("\nOther candidates:")
            for a in resp["alternates"]:
                print(f"  - {a['vendor']:12s} {a['model']}")
        return
    if t == "compare":
        print(resp["message"])
        print()
        print(agent.format_compare(resp["results"]))
        return
    # filter
    res = resp["results"]
    print(f"{resp['message']}  ({len(res)} match)")
    print()
    for r in res:
        sc = r.get("switching_capacity_gbps")
        sc = f"{sc:g} Gbps" if sc else "-"
        print(f"  {r['vendor']:12s} {r['model']:34s} "
              f"{r.get('port_config', '') or '-':<26} {sc}")


def _emit_json(agent: SpecAgent, args) -> None:
    """Single-line JSON output for programmatic callers (Node/Express).

    The last line of stdout is always a JSON object so callers can parse
    it cheaply. Exit code stays 0 even on a miss - the JSON `ok` flag
    carries the success signal.
    """
    t0 = time.time()
    try:
        q = " ".join(args.query) if args.query else ""
        if not q:
            payload = {"ok": False, "error": "query required", "mode": "spec"}
        else:
            resp = agent.answer(q)
            payload = {
                "ok": resp.get("type") in ("spec", "compare", "filter",
                                           "vendors"),
                "mode": "spec",
                "query": q,
                "response": resp,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
    except Exception as exc:  # noqa: BLE001 - surface any failure to caller
        payload = {"ok": False, "error": str(exc),
                   "elapsed_ms": int((time.time() - t0) * 1000)}
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


def main() -> None:
    p = argparse.ArgumentParser(description="Switch specification lookup agent")
    p.add_argument("query", nargs="*", help="Natural language query")
    p.add_argument("--vendor", help="Restrict by vendor")
    p.add_argument("--list", action="store_true", help="List models for vendor")
    p.add_argument("--vendors", action="store_true", help="List all vendors")
    p.add_argument("--compare", nargs=2, metavar=("A", "B"))
    p.add_argument("--filter", action="store_true", help="Structured filter mode")
    p.add_argument("--min-speed", type=int, help="Min port speed (Gbps)")
    p.add_argument("--min-ports", type=int, help="Min port count")
    p.add_argument("--min-poe-w", type=int, help="Min PoE budget (W)")
    p.add_argument("--poe", action="store_true", help="Require PoE")
    p.add_argument("--layer", help="L2 / L2+ / L3")
    p.add_argument("--use-case", help="access/leaf/spine/aggregation/core")
    p.add_argument("--feature", help="Required feature (substring)")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--no-live", action="store_true",
                   help="Disable the web fallback (offline / local DB only)")
    p.add_argument("--deadline", type=float, default=8.5,
                   help="Seconds the web fallback may take (default 8.5)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable debug logging (live fallback, scrapers)")
    p.add_argument("--json", action="store_true",
                   help="Emit a single JSON line on stdout (for programmatic "
                        "callers); suppresses pretty-printing.")
    args = p.parse_args()

    if args.verbose:
        try:
            from logging_config import setup_logging
            setup_logging(verbose=True)
        except Exception:
            import logging
            logging.basicConfig(level=logging.DEBUG)

    agent = SpecAgent(live=not args.no_live, live_deadline=args.deadline)

    if args.json:
        _emit_json(agent, args)
        return

    if args.vendors:
        for vendor, count in agent.list_vendors():
            print(f"  {vendor:20s} {count}")
        return
    if args.list:
        if not args.vendor:
            print("--list requires --vendor")
            sys.exit(1)
        models = agent.list_models(args.vendor)
        for m in models:
            print(f"  {m['model']:40s} {m.get('port_config', '')}")
        print(f"\n{len(models)} models")
        return
    if args.compare:
        resp = agent.answer(f"compare {args.compare[0]} vs {args.compare[1]}")
        render(agent, resp)
        return
    if args.filter:
        results = agent.filter(
            vendor=args.vendor,
            min_port_speed=args.min_speed,
            min_ports=args.min_ports,
            min_poe_w=args.min_poe_w,
            poe=True if args.poe else None,
            layer=args.layer,
            use_case=args.use_case,
            feature=args.feature,
        )
        for r in results[: args.limit]:
            print(f"  {r['vendor']:12s} {r['model']:40s} {r.get('port_config', '')}")
        print(f"\n{len(results)} matches")
        return

    # Natural language (one-shot or REPL)
    if args.query:
        q = " ".join(args.query)
        t0 = time.time()
        render(agent, agent.answer(q))
        print(f"\n[answered in {(time.time() - t0) * 1000:.0f} ms]")
        return

    print("Switch Spec Agent - type a query, or 'quit' to exit.")
    print("Examples: 'Arista 7050SX3-48YC8', 'compare C9300-48P vs EX4400-48P',")
    print("          'which switches support 400G', 'vendors'\n")
    while True:
        try:
            q = input("spec> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in ("quit", "exit", "q"):
            break
        if not q:
            continue
        t0 = time.time()
        render(agent, agent.answer(q))
        print(f"\n[{(time.time() - t0) * 1000:.0f} ms]\n")


if __name__ == "__main__":
    main()
