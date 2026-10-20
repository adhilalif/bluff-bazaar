"""Bluff Bazaar balance simulations (real engine, heuristic bots).

    python scripts/simulate.py report --games 3000 > docs/SIMULATION_REPORT.md

Every experiment uses a fixed seed range, so results are reproducible and A/B comparisons
use matched seeds (same events, hands, lots and line-ups; only the tested setting differs).
"""
from __future__ import annotations

import argparse
import functools
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bluff.config import GameConfig  # noqa: E402
from bluff.sim.bots import STRATEGIES, InsiderTrader  # noqa: E402
from bluff.sim.runner import mean_price_path, mixed_schedule, run, summarize  # noqa: E402

BASE5 = ["passive", "random", "trend", "pump", "insider"]
ACTIVE = ["random", "trend", "pump", "insider"]
SEED0 = 1000


def fmt_table(summary: dict, cols=("mean", "median", "stdev", "win_rate", "coins", "goods")) -> str:
    head = "| Strategy | Games | " + " | ".join(cols) + " |"
    out = [head, "|" + "---|" * (len(cols) + 2)]
    for name, d in sorted(summary["strategies"].items(), key=lambda kv: -kv[1]["mean"]):
        vals = [f'{d[c] * 100:.1f}%' if c == "win_rate" else f"{d[c]:.1f}" for c in cols]
        out.append(f"| {name} | {d['games']} | " + " | ".join(vals) + " |")
    return "\n".join(out)


def health(summary: dict) -> str:
    seats = " / ".join(f"{w * 100:.1f}%" for w in summary["seat_win_rate"])
    return (f"Seat win rates (1–4): {seats} · tie rate {summary['tie_rate'] * 100:.1f}% · "
            f"mean first–last gap {summary['mean_gap']:.1f} · comeback rate {summary['comeback_rate'] * 100:.1f}% · "
            f"illegal bot moves {summary['illegal_moves']} · invariant errors {len(summary['invariant_errors'])}")


def mirror(config: GameConfig, games: int, names=ACTIVE) -> str:
    rows = ["| Strategy (all 4 seats) | Mean net worth | Price moves | Cards & events | Cargo | Spoiled |",
            "|---|---|---|---|---|---|"]
    for n in ["passive"] + list(names):
        s = summarize(run(config, [[n] * 4] * games, STRATEGIES, SEED0))["strategies"][n]
        rows.append(f"| {n} | {s['mean']:.1f} | {s['price_moves']:+.1f} | {s['cards_events']:+.1f} | "
                    f"{s['cargo']:+.1f} | {s['spoiled']:+.1f} |")
    return "\n".join(rows)


def ab(config: GameConfig, games: int, label: str, **changes) -> str:
    lineups = mixed_schedule(BASE5, games)
    a = summarize(run(config, lineups, STRATEGIES, SEED0))
    b = summarize(run(replace(config, **changes), lineups, STRATEGIES, SEED0))
    rows = [f"**{label}** (`{', '.join(f'{k}={v}' for k, v in changes.items())}`), {games} matched-seed games",
            "", "| Strategy | Mean A | Mean B | Δ | Win A | Win B |", "|---|---|---|---|---|---|"]
    for n in BASE5:
        x, y = a["strategies"][n], b["strategies"][n]
        rows.append(f"| {n} | {x['mean']:.1f} | {y['mean']:.1f} | {(y['mean'] / x['mean'] - 1) * 100:+.1f}% | "
                    f"{x['win_rate'] * 100:.1f}% | {y['win_rate'] * 100:.1f}% |")
    rows.append("")
    rows.append(f"Gap A {a['mean_gap']:.1f} → B {b['mean_gap']:.1f}; comeback A {a['comeback_rate'] * 100:.1f}% → "
                f"B {b['comeback_rate'] * 100:.1f}%; tie A {a['tie_rate'] * 100:.1f}% → B {b['tie_rate'] * 100:.1f}%")
    return "\n".join(rows)


def insider_test(config: GameConfig, games: int) -> str:
    """Identical trend+insider bots; only whether Insider Tip is used (or what it reveals) differs."""
    factories = {"tip": functools.partial(InsiderTrader, use_tips=True),
                 "no_tip": functools.partial(InsiderTrader, use_tips=False)}
    for f, n in ((factories["tip"], "tip"), (factories["no_tip"], "no_tip")):
        f.name = n
    base = [["tip", "tip", "no_tip", "no_tip"], ["no_tip", "tip", "tip", "no_tip"],
            ["no_tip", "no_tip", "tip", "tip"], ["tip", "no_tip", "no_tip", "tip"]]
    lineups = [base[i % 4] for i in range(games)]
    rows = ["| Insider Tip reveals | Users mean | Non-users mean | User advantage | User win rate |", "|---|---|---|---|---|"]
    # A "good/bad news only" variant is omitted: the rumor already names the commodity, so the
    # direction alone is the whole secret and it measures identically to the exact reveal.
    for mode in ("full", "off"):
        res = run(config, lineups, factories, SEED0, insider_mode=mode)
        for r in res:  # relabel by flag (bot.name is the class name)
            r.strategies = lineups[r.seed - SEED0]
        s = summarize(res)["strategies"]
        u, n = s["tip"], s["no_tip"]
        label = {"full": "exact event (current)", "off": "nothing (control)"}[mode]
        rows.append(f"| {label} | {u['mean']:.1f} | {n['mean']:.1f} | {u['mean'] - n['mean']:+.1f} | "
                    f"{u['win_rate'] * 100:.1f}% |")
    return "\n".join(rows)


def report(games: int) -> str:
    classic = GameConfig(rules="classic")
    full = GameConfig(rules="full")
    out = ["# Bluff Bazaar — simulation report", "",
           f"Generated by `python scripts/simulate.py report --games {games}`. Real engine, heuristic bots "
           "(see `bluff/sim/bots.py`); seeds start at 1000 and every A/B pair uses identical seeds and line-ups. "
           "Bots are diagnostics, not substitutes for human playtests.", "",
           "Mixed matches: 5 strategies, 4 seats — every 4-strategy subset in every seat rotation, so each "
           "strategy plays 80% of games. **Win rate = share of the games it played that it won; ties split "
           "evenly** (so the column does not sum to 100%).", ""]

    lineups = mixed_schedule(BASE5, games)
    cs = summarize(run(classic, lineups, STRATEGIES, SEED0))
    out += ["## 1. Baseline — Classic rules (current live game)", "", fmt_table(cs), "", health(cs), "",
            "### Mirror matches (all four seats the same strategy) with wealth sources", "",
            "Wealth sources are per-player sums over 8 rounds from the engine's net-worth breakdown; "
            "they reconcile exactly to final net worth − 100.", "", mirror(classic, games), "",
            "### Card plays (mixed)", "", ", ".join(f"{k} {v}" for k, v in sorted(cs["card_plays"].items())), "",
            "### Mean price path (start → after each round)", ""]
    for c, path in mean_price_path(run(classic, lineups, STRATEGIES, SEED0)).items():
        out.append(f"- {c}: {classic.starting_prices[c]} → " + " → ".join(f"{p:.1f}" for p in path))
    out += ["", "## 2. Balance A/B tests (Classic rules, matched seeds) — not applied", ""]
    for label, ch in (("Lower demand impact", {"demand_divisor": 3, "demand_cap": 3}),
                      ("Lower maximum swing", {"max_swing_pct": 30}),
                      ("Lower card movement", {"card_move_pct": 20})):
        out += [ab(classic, games, label, **ch), ""]
    out += ["## 3. Insider Tip controlled test", "",
            "All four bots run identical trend+insider logic; two (rotating seats) use their Insider Tip and two "
            "never do. Only what the tip reveals changes between rows.", "", insider_test(classic, games), ""]

    names = BASE5 + ["order"]
    fl = mixed_schedule(names, games)
    fs = summarize(run(full, fl, STRATEGIES, SEED0))
    out += ["## 4. Full Bazaar rules (orders, perishables, cargo auction, monsoon)", "",
            "Mixed matches with the order-chasing bot added (6 strategies, 4 seats).", "",
            fmt_table(fs, ("mean", "median", "stdev", "win_rate", "bonus")), "", health(fs), "",
            "### Mirror matches with wealth sources", "", mirror(full, games, ACTIVE + ["order"]), "",
            "### Secret order completion (share of games the holder met it)", "",
            ", ".join(f"{k} {v * 100:.0f}%" for k, v in fs["order_success"].items()), "",
            "### Classic vs Full (same seeds, line-ups, bots)", "", "| Strategy | Classic | Full |", "|---|---|---|"]
    cs2 = summarize(run(classic, lineups, STRATEGIES, SEED0))
    fs2 = summarize(run(full, lineups, STRATEGIES, SEED0))
    for n in BASE5:
        out.append(f"| {n} | {cs2['strategies'][n]['mean']:.1f} / {cs2['strategies'][n]['win_rate'] * 100:.1f}% | "
                   f"{fs2['strategies'][n]['mean']:.1f} / {fs2['strategies'][n]['win_rate'] * 100:.1f}% |")
    out += ["", health(fs2)]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="full markdown report")
    r.add_argument("--games", type=int, default=3000)
    a = sub.add_parser("ab", help="one matched-seed A/B test")
    a.add_argument("--games", type=int, default=3000)
    a.add_argument("--rules", choices=("classic", "full"), default="classic")
    a.add_argument("--set", action="append", default=[], help="config change, e.g. demand_divisor=3")
    args = ap.parse_args()
    if args.cmd == "report":
        print(report(args.games))
    else:
        changes = {k: int(v) for k, v in (kv.split("=", 1) for kv in args.set)}
        print(ab(GameConfig(rules=args.rules), args.games, "A/B", **changes))


if __name__ == "__main__":
    main()
