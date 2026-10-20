"""Presentation helpers: theme CSS and small HTML snippets for Streamlit.

Anything derived from user input (player names) MUST go through `esc()`.
"""
from __future__ import annotations

from html import escape
from typing import Any, Optional

from .config import CARD_INFO, COMMODITIES, COMMODITY_INFO, event_info

CSS = """
<style>
:root {
  --bb-navy: #0b1328; --bb-panel: #14203d; --bb-panel-2: #1a2a4f; --bb-line: #26365e;
  --bb-teal: #2dd4bf; --bb-gold: #fbbf24; --bb-text: #e8edf7; --bb-muted: #94a3c0;
  --bb-up: #34d399; --bb-down: #f87171;
}
.stApp { background: radial-gradient(1200px 600px at 50% -200px, #18294f 0%, var(--bb-navy) 60%); }
.block-container { max-width: 760px; padding-top: 1.2rem; padding-bottom: 4rem; }
header[data-testid="stHeader"] { background: transparent; }
h1, h2, h3 { letter-spacing: .01em; }
.bb-title { text-align:center; margin: .2rem 0 1rem; }
.bb-title .logo { font-size: 2.6rem; line-height: 1; }
.bb-title h1 { margin: .2rem 0 0; font-size: 2rem;
  background: linear-gradient(90deg, var(--bb-teal), var(--bb-gold)); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.bb-title p { color: var(--bb-muted); margin: .2rem 0 0; }
.bb-panel { background: var(--bb-panel); border: 1px solid var(--bb-line); border-radius: 16px;
  padding: .8rem 1rem; margin: .4rem 0 .8rem; }
.bb-row { display:flex; gap:.6rem; align-items:center; justify-content:space-between; flex-wrap:wrap; }
.bb-chip { display:inline-flex; align-items:center; gap:.3rem; background: var(--bb-panel-2);
  border:1px solid var(--bb-line); border-radius: 999px; padding: .15rem .6rem; font-size:.85rem; margin:.15rem; }
.bb-chip.gold { border-color: rgba(251,191,36,.5); color: var(--bb-gold); }
.bb-chip.teal { border-color: rgba(45,212,191,.5); color: var(--bb-teal); }
.bb-muted { color: var(--bb-muted); font-size: .85rem; }
.bb-code { font-family: ui-monospace, monospace; font-size: 2.2rem; letter-spacing: .35rem; color: var(--bb-gold);
  text-align:center; padding:.3rem 0; }
.bb-market { display:grid; grid-template-columns: repeat(5, 1fr); gap:.45rem; margin:.4rem 0 .8rem; }
.bb-tile { background: linear-gradient(160deg, var(--bb-panel-2), var(--bb-panel)); border:1px solid var(--bb-line);
  border-radius: 14px; padding:.45rem .3rem; text-align:center; }
.bb-tile .ic { font-size:1.4rem; line-height:1.2; }
.bb-tile .nm { color: var(--bb-muted); font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; }
.bb-tile .pr { font-size:1.25rem; font-weight:700; color: var(--bb-text); }
.bb-tile .ft { font-size:.72rem; }
.bb-tile .own { color: var(--bb-teal); }
.up { color: var(--bb-up); } .down { color: var(--bb-down); } .flat { color: var(--bb-muted); }
.bb-rumor { border-left: 4px solid var(--bb-gold); background: rgba(251,191,36,.07); border-radius: 12px;
  padding: .6rem .9rem; margin: .4rem 0 .8rem; }
.bb-rumor b { color: var(--bb-gold); }
.bb-cards { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:.5rem; }
.bb-card { background: linear-gradient(170deg, #1f3263, #142246); border: 1px solid rgba(45,212,191,.35);
  border-radius: 14px; padding:.6rem .7rem; box-shadow: 0 6px 18px rgba(0,0,0,.25); }
.bb-card .ic { font-size:1.5rem; } .bb-card .nm { font-weight:700; color: var(--bb-gold); }
.bb-card .tx { font-size:.8rem; color: var(--bb-muted); }
.bb-players { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:.45rem; }
.bb-player { background: var(--bb-panel); border:1px solid var(--bb-line); border-radius: 12px; padding:.45rem .6rem; }
.bb-player.me { border-color: var(--bb-teal); }
.bb-player .nm { font-weight:600; } .bb-player .st { font-size:.78rem; color: var(--bb-muted); }
.dot { display:inline-block; width:.55rem; height:.55rem; border-radius:50%; margin-right:.3rem; }
.dot.on { background: var(--bb-up); } .dot.off { background: #64748b; }
.bb-stat { text-align:center; } .bb-stat .v { font-size:1.4rem; font-weight:700; color: var(--bb-gold); }
.bb-stat .l { font-size:.75rem; color: var(--bb-muted); text-transform:uppercase; letter-spacing:.06em; }
.bb-locked { text-align:center; border:1px dashed var(--bb-teal); border-radius:14px; padding:.9rem; margin:.4rem 0 .8rem;
  background: rgba(45,212,191,.06); }
.bb-podium { display:flex; align-items:flex-end; justify-content:center; gap:.5rem; margin: 1rem 0; }
.bb-podium .col { flex:1; max-width: 200px; text-align:center; }
.bb-podium .blk { border-radius: 14px 14px 4px 4px; padding:.6rem .3rem; font-weight:700; }
.bb-podium .p1 .blk { height: 130px; background: linear-gradient(180deg, #fbbf24, #b7791f); color:#1b1300; }
.bb-podium .p2 .blk { height: 95px; background: linear-gradient(180deg, #2dd4bf, #0f766e); color:#03221e; }
.bb-podium .p3 .blk { height: 70px; background: linear-gradient(180deg, #64748b, #334155); }
.bb-winner { text-align:center; font-size:1.6rem; font-weight:800; margin-top:.4rem;
  background: linear-gradient(90deg, var(--bb-gold), var(--bb-teal)); -webkit-background-clip:text;
  background-clip:text; color: transparent; }
.bb-log { font-size:.88rem; line-height:1.5; }
.bb-log .who { font-weight:600; }
div[data-testid="stProgress"] > div > div > div > div { background: linear-gradient(90deg, var(--bb-teal), var(--bb-gold)); }
.bb-stats { display:grid; grid-template-columns: repeat(3, 1fr); gap:.5rem; margin:.2rem 0 .8rem; }
.bb-stats .bb-panel { margin:0; padding:.55rem .3rem; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primaryFormSubmit"],
.stFormSubmitButton > button[kind="primary"] { background: linear-gradient(90deg, #14b8a6, #2dd4bf); color: #04201c; border: 0;
  font-weight: 700; }
@media (max-width: 560px) {
  .bb-market { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 480px) {
  .block-container { padding-left: .8rem; padding-right: .8rem; }
  .bb-code { font-size: 1.8rem; }
  .bb-tile .pr { font-size: 1.15rem; }
}
</style>
"""


def esc(value: Any) -> str:
    return escape(str(value), quote=True)


def commodity_label(c: Optional[str]) -> str:
    if not c:
        return ""
    info = COMMODITY_INFO[c]
    return f"{info['icon']} {info['label']}"


def card_label(card_type: Optional[str]) -> str:
    if not card_type:
        return "No card"
    info = CARD_INFO[card_type]
    return f"{info['icon']} {info['name']}"


def delta_html(before: Optional[int], after: int) -> str:
    if before is None or before == after:
        return '<span class="flat">—</span>'
    d = after - before
    cls, arrow = ("up", "▲") if d > 0 else ("down", "▼")
    return f'<span class="{cls}">{arrow} {abs(d)}</span>'


def stats_html(stats: list[tuple[Any, str]]) -> str:
    cells = "".join(f'<div class="bb-panel bb-stat"><div class="v">{esc(v)}</div><div class="l">{esc(label)}</div></div>'
                    for v, label in stats)
    return f'<div class="bb-stats">{cells}</div>'


def title_html() -> str:
    return ('<div class="bb-title"><div class="logo">🏺💎⚡</div><h1>Bluff Bazaar</h1>'
            '<p>Trade, scheme and bluff your way to the richest stall in the market.</p></div>')


def market_html(prices: dict[str, int], previous: Optional[dict[str, int]], inventory: dict[str, int]) -> str:
    tiles = []
    for c in COMMODITIES:
        info = COMMODITY_INFO[c]
        own = inventory.get(c, 0)
        tiles.append(
            f'<div class="bb-tile"><div class="ic">{info["icon"]}</div><div class="nm">{info["label"]}</div>'
            f'<div class="pr">{prices[c]}</div><div class="ft">{delta_html(previous.get(c) if previous else None, prices[c])}'
            f'{f" · <span class=own>×{own}</span>" if own else ""}</div></div>')
    return f'<div class="bb-market">{"".join(tiles)}</div>'


def cards_html(cards: list[dict]) -> str:
    if not cards:
        return '<div class="bb-muted">No cards left in your hand.</div>'
    out = [f'<div class="bb-card"><div class="ic">{c["icon"]}</div><div class="nm">{esc(c["name"])}</div>'
           f'<div class="tx">{esc(c["text"])}</div></div>' for c in cards]
    return f'<div class="bb-cards">{"".join(out)}</div>'


def players_html(players: list[dict], scores: dict[str, int], in_game: bool) -> str:
    out = []
    for p in players:
        status = []
        if in_game:
            status.append("✅ locked in" if p["submitted"] else "⏳ deciding")
        else:
            status.append("✅ ready" if p["is_ready"] else "⏳ not ready")
        if p["is_host"]:
            status.append("👑 host")
        score = scores.get(p["player_id"])
        out.append(
            f'<div class="bb-player{" me" if p["is_me"] else ""}">'
            f'<div class="nm"><span class="dot {"on" if p["online"] else "off"}"></span>'
            f'{esc(p["name"])}{" (you)" if p["is_me"] else ""}</div>'
            f'<div class="st">Seat {p["seat"]} · {" · ".join(status)}</div>'
            + (f'<div class="st">Net worth <b style="color:var(--bb-gold)">{score}</b></div>' if score is not None else "")
            + "</div>")
    return f'<div class="bb-players">{"".join(out)}</div>'


def trade_text(trade: dict) -> str:
    if trade["action"] == "hold":
        return "held" + (" (timed out)" if trade.get("auto") else "")
    verb = "bought" if trade["action"] == "buy" else "sold"
    text = f'{verb} {trade["quantity"]} {commodity_label(trade["commodity"])}'
    if trade["executed"]:
        return f'{text} @ {trade["price"]}'
    return f'tried to {trade["action"]} {trade["quantity"]} {commodity_label(trade["commodity"])} (failed)'


EFFECT_TEXT = {
    "double_down": "🎲 Double Down",
    "shield_refund": "🛡️ Shield refund",
    "festival": "🎉 Festival bonus",
    "customs": "🛃 Customs",
    "tax_collected": "💰 Taxed",
    "tax_paid": "💰 Tax paid",
}


def summary_html(summary: dict, names: dict[str, str]) -> str:
    ev = event_info(summary["event"])
    lines = [f'<div class="bb-rumor"><b>{ev["icon"]} {esc(ev["name"])}</b> — {esc(ev["text"])}</div>']
    moves = []
    for c in COMMODITIES:
        m = summary["movement"][c]
        moves.append(f'<span class="bb-chip">{COMMODITY_INFO[c]["icon"]} {m["before"]} → <b>{m["after"]}</b> '
                     f'{delta_html(m["before"], m["after"])}</span>')
    lines.append(f'<div>{"".join(moves)}</div>')
    for row in summary["players"]:
        parts = [trade_text(row["trade"])]
        card = row.get("card")
        if card:
            target = ""
            if card.get("target_commodity"):
                target = f' on {commodity_label(card["target_commodity"])}'
            elif card.get("target_player"):
                target = f' on {esc(names.get(card["target_player"], "?"))}'
            parts.append(f'played {card_label(card["type"])}{target}')
        if row.get("insider_tip"):
            parts.append("consulted an 🔍 Insider")
        effects = []
        for e in row["effects"]:
            label = EFFECT_TEXT.get(e["kind"], e["kind"])
            amount = f'{e["amount"]:+d}' if e["amount"] else (e.get("note") or "0")
            effects.append(f'{label} {esc(amount)}')
        effect_html = f' <span class="bb-muted">({", ".join(effects)})</span>' if effects else ""
        lines.append(f'<div class="bb-log"><span class="who">{esc(row["name"])}</span> {"; ".join(parts)}'
                     f'{effect_html} → <b style="color:var(--bb-gold)">{row["net_worth"]}</b></div>')
    return "".join(lines)


def podium_html(results: list[dict]) -> str:
    top = results[:3]
    order = [1, 0, 2] if len(top) == 3 else list(range(len(top)))
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}
    cols = []
    for i in order:
        r = top[i]
        place = min(i + 1, 3)
        cols.append(f'<div class="col p{place}"><div>{medal.get(r["rank"], "🏅")}</div>'
                    f'<div style="font-weight:700">{esc(r["name"])}</div>'
                    f'<div class="blk">{r["net_worth"]} 🪙</div></div>')
    return f'<div class="bb-podium">{"".join(cols)}</div>'
