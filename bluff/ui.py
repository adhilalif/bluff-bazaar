"""Presentation helpers: Kerala-themed CSS and small HTML snippets for Streamlit.

All player-facing names come from bluff/theme.py. Anything derived from user input
(player names) MUST go through `esc()`.
"""
from __future__ import annotations

from html import escape
from typing import Any, Optional

from . import theme
from .config import ORDERS
from .theme import CARD_DISPLAY, COMMODITY_DISPLAY, DISPLAY_ORDER, ORDER_DISPLAY, SEASON_DISPLAY

# Palette: kasavu ivory, forest green, antique gold, teak brown.
CSS = """
<style>
:root {
  --bb-ivory: #FBF7EC; --bb-ivory-2: #F4ECD8; --bb-card: #FFFDF7; --bb-line: #E3D6B6;
  --bb-green: #1E4D36; --bb-green-2: #2F6B4C; --bb-green-soft: #E4EEE5;
  --bb-gold: #B08A3E; --bb-gold-soft: #D9B867; --bb-gold-text: #7A5C1C;
  --bb-teak: #6B4226; --bb-teak-dark: #4A2E1A; --bb-text: #2E2015; --bb-muted: #6A5843;
  --bb-up: #1F7A4D; --bb-down: #B0362A;
  --bb-kasavu: linear-gradient(180deg, var(--bb-gold-soft) 0 2px, transparent 2px 4px, var(--bb-gold) 4px 6px);
  --bb-heading: "Fraunces", Georgia, "Noto Serif Malayalam", serif;
  --bb-maxw: 680px;
}
/* Page width + iOS safe areas (no fixed widths anywhere). */
[data-testid="stMainBlockContainer"] {
  max-width: var(--bb-maxw); padding-top: 1rem;
  padding-left: max(1rem, env(safe-area-inset-left)); padding-right: max(1rem, env(safe-area-inset-right));
  padding-bottom: calc(3rem + env(safe-area-inset-bottom));
}
header[data-testid="stHeader"] { background: transparent; }
/* iOS Safari zooms into inputs smaller than 16px. */
input, textarea, select { font-size: 16px !important; }
/* Touch targets of at least 44 CSS px. */
.stButton button, .stFormSubmitButton button, [data-testid="stPopover"] button,
[data-testid="stButtonGroup"] button, [role="tab"] { min-height: 44px; }
[data-testid="stNumberInput"] button { min-height: 44px; min-width: 44px; }
[data-testid="stNumberInput"] input { min-height: 44px; }
[data-testid="stRadio"] label, [data-testid="stCheckbox"] label { min-height: 44px; align-items: center; }
/* Button groups wrap instead of hiding options in a sideways scroller on narrow phones. */
[data-testid="stButtonGroup"] div { flex-wrap: wrap; overflow-x: visible; row-gap: .4rem; }
[data-testid="stRadio"] [role="radiogroup"] { gap: .1rem .9rem; }
/* Visible keyboard focus. */
button:focus-visible, a:focus-visible, input:focus-visible, [role="tab"]:focus-visible,
label:has(input:focus-visible) { outline: 3px solid var(--bb-gold) !important; outline-offset: 2px; border-radius: 8px; }

.bb-brand { display:flex; align-items:center; gap:.7rem; padding:.2rem 0 .55rem; position:relative; margin-bottom:.7rem; }
.bb-brand::after { content:""; position:absolute; left:0; right:0; bottom:0; height:6px; background: var(--bb-kasavu); }
.bb-brand img { width: 46px; height: 46px; flex: none; }
.bb-brand .t { font-family: var(--bb-heading); font-weight: 700; font-size: 1.7rem; color: var(--bb-green); line-height:1.05; }
.bb-brand .s { font-size: .82rem; letter-spacing: .14em; text-transform: uppercase; color: var(--bb-gold-text); }
.bb-brand.big { flex-direction: column; text-align: center; gap: .25rem; padding-bottom: .9rem; }
.bb-brand.big img { width: 64px; height: 64px; }
.bb-brand.big .t { font-size: 2.3rem; }
.bb-intro { text-align:center; color: var(--bb-muted); margin: -.2rem 0 .8rem; }

.bb-panel { background: var(--bb-card); border: 1px solid var(--bb-line); border-radius: 16px;
  padding: .75rem .9rem; margin: .35rem 0 .75rem; box-shadow: 0 1px 0 rgba(107,66,38,.06); }
.bb-panel.kasavu { border-top: 0; position: relative; padding-top: 1rem; overflow: hidden; }
.bb-panel.kasavu::before { content:""; position:absolute; left:0; right:0; top:0; height:6px; background: var(--bb-kasavu); }
.bb-h { font-family: var(--bb-heading); font-weight: 650; color: var(--bb-green); font-size: 1.15rem; margin: .5rem 0 .3rem; }
.bb-row { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap; }
.bb-chip { display:inline-flex; align-items:center; gap:.3rem; background: var(--bb-ivory-2); color: var(--bb-text);
  border:1px solid var(--bb-line); border-radius: 999px; padding: .2rem .65rem; font-size:.88rem; min-height: 30px; }
.bb-chip.gold { border-color: var(--bb-gold); color: var(--bb-gold-text); font-weight: 600; }
.bb-chip.green { background: var(--bb-green-soft); border-color: #B9D2BF; color: var(--bb-green); font-weight: 600; }
.bb-chip.warn { background: #FBE9E5; border-color: #E9B8AE; color: var(--bb-down); font-weight: 600; }
.bb-muted { color: var(--bb-muted); font-size: .88rem; }
.bb-en { color: var(--bb-muted); font-size: .76rem; }
.bb-code { font-family: ui-monospace, Menlo, monospace; font-size: 2.1rem; letter-spacing: .3rem; color: var(--bb-teak);
  text-align:center; padding:.2rem 0; font-weight: 700; }

/* Market tiles: 3 across in narrow containers, 5 across when there is room. */
.bb-cq { container-type: inline-size; }
.bb-market { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:.45rem; margin:.3rem 0 .7rem; }
@container (min-width: 500px) { .bb-market { grid-template-columns: repeat(5, minmax(0, 1fr)); } }
.bb-tile { background: var(--bb-card); border:1px solid var(--bb-line); border-radius: 14px; padding:.5rem .3rem .45rem;
  text-align:center; position: relative; overflow: hidden; }
.bb-tile::before { content:""; position:absolute; left:0; right:0; top:0; height:4px; background: var(--bb-kasavu); opacity:.8; }
.bb-tile img { width: 40px; height: 40px; display:block; margin: .15rem auto .1rem; }
.bb-tile .nm { font-weight: 700; color: var(--bb-teak-dark); font-size: .95rem; line-height: 1.15; overflow-wrap:anywhere; }
.bb-tile .pr { font-size:1.3rem; font-weight:800; color: var(--bb-green); line-height: 1.2; }
.bb-tile .ft { font-size:.76rem; min-height: 1.1em; }
.bb-tile .own { color: var(--bb-gold-text); font-weight: 700; }
@container (max-width: 499px) {
  .bb-tile { padding: .4rem .2rem .3rem; }
  .bb-tile img { width: 30px; height: 30px; margin: .1rem auto 0; }
  .bb-tile .nm { font-size: .88rem; }
  .bb-tile .pr { font-size: 1.15rem; }
}
@container (max-width: 340px) {
  .bb-tile .nm { font-size: .78rem; letter-spacing: -.01em; }
  .bb-tile .bb-en { font-size: .68rem; }
}
.bb-tile .rot { font-size:.68rem; color: var(--bb-muted); }
.bb-tile .rot.warn { color: var(--bb-down); font-weight: 700; }
.bb-order { border:1px solid #E8D29B; background: #FBF1DA; border-radius: 14px; padding:.55rem .8rem; margin:.1rem 0 .7rem; }
.bb-order .t { font-family: var(--bb-heading); font-weight: 650; color: var(--bb-teak-dark); }
.bb-order .p { font-size:.85rem; }
.bb-order .ok { color: var(--bb-up); font-weight: 700; }
.bb-break { font-size:.84rem; color: var(--bb-muted); margin-top:.2rem; }
.up { color: var(--bb-up); font-weight: 700; } .down { color: var(--bb-down); font-weight: 700; } .flat { color: var(--bb-muted); }

/* Rumor: a monsoon-streaked notice. */
.bb-rumor { border-left: 5px solid var(--bb-green-2); border-radius: 12px; padding: .65rem .9rem; margin: .3rem 0 .7rem;
  background: repeating-linear-gradient(105deg, rgba(47,107,76,.07) 0 2px, transparent 2px 11px), var(--bb-green-soft); }
.bb-rumor b { color: var(--bb-green); }
.bb-rumor.insider { border-left-color: var(--bb-gold); background: #FBF1DA; }
.bb-rumor.insider b { color: var(--bb-gold-text); }

.bb-stats { display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:.45rem; margin:.1rem 0 .7rem; }
.bb-stat { background: var(--bb-card); border:1px solid var(--bb-line); border-radius: 14px; text-align:center; padding:.4rem .2rem; }
.bb-stat .v { font-size:1.3rem; font-weight:800; color: var(--bb-teak); }
.bb-stat .l { font-size:.72rem; color: var(--bb-muted); text-transform:uppercase; letter-spacing:.07em; }

/* Cards: teak-wood header strip, ivory body. */
.bb-cards { display:grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 210px), 1fr)); gap:.5rem; }
.bb-card { background: var(--bb-card); border: 1px solid var(--bb-line); border-radius: 14px; overflow: hidden; }
.bb-card .top { background: linear-gradient(90deg, var(--bb-teak-dark), var(--bb-teak)); color: #FBF3DD; padding: .45rem .7rem;
  font-family: var(--bb-heading); font-weight: 650; display:flex; gap:.4rem; align-items:center; }
.bb-card .bd { padding: .45rem .7rem .6rem; font-size: .86rem; }

.bb-players { display:grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 150px), 1fr)); gap:.45rem; }
.bb-player { background: var(--bb-card); border:1px solid var(--bb-line); border-radius: 12px; padding:.5rem .65rem; }
.bb-player.me { border: 2px solid var(--bb-green-2); }
.bb-player .nm { font-weight:700; overflow-wrap:anywhere; } .bb-player .st { font-size:.8rem; color: var(--bb-muted); }
.dot { display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-right:.3rem; vertical-align: middle; }
.dot.on { background: var(--bb-up); } .dot.off { background: #A39580; }

/* Live lock strip (updated in place by the poll fragment). */
.bb-strip { display:grid; grid-template-columns: repeat(var(--bb-seats, 4), minmax(0, 1fr)); gap:.35rem; margin:.35rem 0 .2rem; }
.bb-seat { border:1px solid var(--bb-line); border-radius: 10px; padding:.3rem .35rem; text-align:center; font-size:.8rem;
  background: var(--bb-card); overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.bb-seat.done { background: var(--bb-green-soft); border-color: #B9D2BF; color: var(--bb-green); font-weight: 700; }
.bb-seat.me { box-shadow: inset 0 0 0 2px var(--bb-gold-soft); }
.bb-progress { height: 8px; border-radius: 99px; background: var(--bb-ivory-2); overflow:hidden; margin:.4rem 0 .2rem;
  border: 1px solid var(--bb-line); }
.bb-progress > div { height: 100%; background: linear-gradient(90deg, var(--bb-green-2), var(--bb-gold)); }

.bb-locked { text-align:center; border:2px dashed var(--bb-green-2); border-radius:14px; padding:.9rem; margin:.3rem 0 .7rem;
  background: var(--bb-green-soft); }
.bb-result { border-radius: 14px; padding: .6rem .8rem; margin: .2rem 0 .7rem; background: #FBF1DA; border:1px solid #E8D29B; }

.bb-log { font-size:.9rem; line-height:1.5; padding:.3rem 0; border-bottom: 1px dashed var(--bb-line); }
.bb-log:last-child { border-bottom: 0; }
.bb-log .who { font-weight:700; }

.bb-podium { display:flex; align-items:flex-end; justify-content:center; gap:.5rem; margin: .8rem 0; }
.bb-podium .col { flex:1; max-width: 190px; text-align:center; min-width: 0; }
.bb-podium .nm { font-weight: 700; overflow-wrap:anywhere; }
.bb-podium .blk { border-radius: 14px 14px 4px 4px; padding:.6rem .3rem; font-weight:800; color: #FFFDF7; }
.bb-podium .p1 .blk { height: 120px; background: linear-gradient(180deg, #C9A24E, var(--bb-gold)); }
.bb-podium .p2 .blk { height: 90px; background: linear-gradient(180deg, var(--bb-green-2), var(--bb-green)); }
.bb-podium .p3 .blk { height: 68px; background: linear-gradient(180deg, #8A5A36, var(--bb-teak)); }
.bb-winner { text-align:center; font-family: var(--bb-heading); font-size:1.65rem; font-weight:700; color: var(--bb-green); margin-top:.3rem; }
.bb-react { text-align:center; font-size: 1.15rem; color: var(--bb-gold-text); font-weight: 700; }

/* Results table -> stacked cards on phones. */
.bb-table { width:100%; border-collapse: separate; border-spacing: 0; font-size:.95rem; background: var(--bb-card);
  border:1px solid var(--bb-line); border-radius: 14px; overflow:hidden; }
.bb-table th, .bb-table td { padding:.55rem .6rem; text-align:right; border-bottom: 1px solid var(--bb-line); }
.bb-table th { background: var(--bb-ivory-2); color: var(--bb-muted); font-weight: 600; font-size: .8rem; text-transform: uppercase; }
.bb-table td:nth-child(2), .bb-table th:nth-child(2) { text-align:left; }
.bb-table tr:last-child td { border-bottom: 0; }
@media (max-width: 560px) {
  .bb-table thead { display:none; }
  .bb-table, .bb-table tbody, .bb-table tr, .bb-table td { display:block; width:100%; }
  .bb-table tr { border-bottom: 1px solid var(--bb-line); padding: .35rem 0; }
  .bb-table td { border: 0; display:flex; justify-content: space-between; padding: .2rem .8rem; text-align:right; }
  .bb-table td::before { content: attr(data-label); color: var(--bb-muted); font-size:.82rem; }
  .bb-code { font-size: 1.8rem; }
  .bb-brand .t { font-size: 1.45rem; }
}
</style>
"""


def page_width_css(max_width: str) -> str:
    """Per-page content width (narrow for home/lobby, wide for the game table)."""
    return f"<style>:root {{ --bb-maxw: {max_width}; }}</style>"


def esc(value: Any) -> str:
    return escape(str(value), quote=True)


def en_line(text: str, english: bool) -> str:
    return f'<div class="bb-en">{esc(text)}</div>' if english else ""


def brand_html(big: bool = False) -> str:
    return (f'<div class="bb-brand{" big" if big else ""}"><img src="{theme.icon_data_uri("uru")}" alt="">'
            f'<div><div class="t">{esc(theme.BRAND_TITLE)}</div><div class="s">{esc(theme.BRAND_TAGLINE)}</div></div></div>')


def delta_html(before: Optional[int], after: int) -> str:
    if before is None or before == after:
        return '<span class="flat">—</span>'
    d = after - before
    cls, arrow = ("up", "▲") if d > 0 else ("down", "▼")
    return f'<span class="{cls}">{arrow} {abs(d)}</span>'


def market_html(prices: dict[str, int], previous: Optional[dict[str, int]], inventory: dict[str, int],
                english: bool, rules: Optional[dict] = None) -> str:
    """Market tiles. `rules` is the public round-rules dict (perishables, spoil threshold, unsellable)."""
    rules = rules or {}
    perishables = set(rules.get("perishables") or ())
    threshold = rules.get("spoil_threshold")
    unsellable = set(rules.get("unsellable") or ())
    tiles = []
    for c in DISPLAY_ORDER:
        d = COMMODITY_DISPLAY[c]
        own = inventory.get(c, 0)
        prev = previous.get(c) if previous else None
        note = ""
        if c in perishables and threshold:
            if own >= threshold:
                note = f'<div class="rot warn">⚠ 1 spoils at round end</div>'
            else:
                note = f'<div class="rot">🍂 spoils at {threshold}+</div>'
        if c in unsellable:
            note += '<div class="rot warn">🔒 no selling</div>'
        tiles.append(
            f'<div class="bb-tile"><img src="{theme.commodity_icon(c)}" alt="{esc(d["name"])} ({esc(d["en"])})">'
            f'<div class="nm">{esc(d["name"])}</div>{en_line(d["en"], english)}'
            f'<div class="pr">{prices[c]}</div><div class="ft">{delta_html(prev, prices[c])}'
            f'{f" · <span class=own>you ×{own}</span>" if own else ""}</div>{note}</div>')
    return f'<div class="bb-cq"><div class="bb-market">{"".join(tiles)}</div></div>'


def stats_html(stats: list[tuple[Any, str]]) -> str:
    cells = "".join(f'<div class="bb-stat"><div class="v">{esc(v)}</div><div class="l">{esc(label)}</div></div>'
                    for v, label in stats)
    return f'<div class="bb-stats">{cells}</div>'


def cards_html(hand: list[dict], english: bool) -> str:
    if not hand:
        return '<div class="bb-muted">No cards left in your hand.</div>'
    out = []
    for c in hand:
        d = CARD_DISPLAY[c["card_type"]]
        out.append(f'<div class="bb-card"><div class="top"><span>{d["icon"]}</span><span>{esc(d["name"])}</span></div>'
                   f'<div class="bd">{en_line(d["en"], english)}{esc(d["text"])}</div></div>')
    return f'<div class="bb-cards">{"".join(out)}</div>'


def players_html(players: list[dict], scores: dict[str, int], lobby: bool) -> str:
    out = []
    for p in players:
        status = [f'Seat {p["seat"]}']
        if lobby:
            status.append("✅ ready" if p["is_ready"] else "⏳ not ready")
        if p["is_host"]:
            status.append("host")
        if not p["online"]:
            status.append("offline")
        score = scores.get(p["player_id"])
        out.append(
            f'<div class="bb-player{" me" if p["is_me"] else ""}">'
            f'<div class="nm"><span class="dot {"on" if p["online"] else "off"}" aria-hidden="true"></span>'
            f'{esc(p["name"])}{" (you)" if p["is_me"] else ""}</div>'
            f'<div class="st">{" · ".join(status)}</div>'
            + (f'<div class="st">Net worth <b style="color:var(--bb-teak)">{score}</b></div>' if score is not None else "")
            + "</div>")
    return f'<div class="bb-players">{"".join(out)}</div>'


def lock_strip_html(players: list[dict], submitted: list[int], online: list[int]) -> str:
    cells = []
    for p in players:
        done = p["seat"] in submitted
        mark = "✓" if done else ("…" if p["seat"] in online else "⚠ offline")
        cls = "bb-seat" + (" done" if done else "") + (" me" if p["is_me"] else "")
        label = "You" if p["is_me"] else p["name"]
        cells.append(f'<div class="{cls}">{esc(label)} {mark}</div>')
    return (f'<div class="bb-strip" style="--bb-seats:{len(players)}" role="status" aria-live="polite">'
            f'{"".join(cells)}</div>')


def progress_html(fraction: float) -> str:
    pct = max(0.0, min(1.0, fraction)) * 100
    return f'<div class="bb-progress" aria-hidden="true"><div style="width:{pct:.1f}%"></div></div>'


def trade_text(trade: dict) -> str:
    if trade["action"] == "hold":
        return "held" + (" (timed out)" if trade.get("auto") else "")
    verb = "bought" if trade["action"] == "buy" else "sold"
    name = theme.commodity_name(trade["commodity"])
    if trade["executed"]:
        return f'{verb} {trade["quantity"]} {name} @ {trade["price"]}'
    return f'tried to {trade["action"]} {trade["quantity"]} {name} (failed)'


EFFECT_TEXT = {
    "double_down": "🎲 Iratti Labham",
    "shield_refund": "🛡️ Raksha refund",
    "festival": "🌼 Onam bonus",
    "customs": "⚓ Customs",
    "tax_collected": "💰 Chungam",
    "tax_paid": "💰 Chungam paid",
    "spoiled": "🍂 Spoiled",
}


def summary_html(summary: dict, names: dict[str, str], english: bool) -> str:
    ev = theme.event_display(summary["event"])
    en = f' <span class="bb-en">({esc(ev["en"])})</span>' if english else ""
    lines = [f'<div class="bb-rumor"><b>{esc(theme.event_title(summary["event"]))}</b>{en} — {esc(ev["text"])}</div>']
    moves = []
    for c in DISPLAY_ORDER:
        m = summary["movement"][c]
        moves.append(f'<span class="bb-chip">{esc(theme.commodity_name(c))} {m["before"]} → <b>{m["after"]}</b> '
                     f'{delta_html(m["before"], m["after"])}</span>')
    lines.append(f'<div class="bb-row" style="margin-bottom:.4rem">{"".join(moves)}</div>')
    auction = summary.get("auction")
    if auction:
        lot = f'{auction["quantity"]} × {esc(theme.commodity_name(auction["commodity"]))}'
        bids = ", ".join(f'{esc(names.get(pid, "?"))} {b}' for pid, b in auction["bids"].items()) or "no bids"
        if auction["winner"]:
            result = f'won by <b>{esc(names.get(auction["winner"], "?"))}</b> for {auction["price"]}'
        elif auction["outcome"] == "tie":
            result = "tied — nobody gets it"
        else:
            result = "unsold"
        lines.append(f'<div class="bb-log">⛵ {esc(theme.CARGO_TITLE)} {lot}: {result} '
                     f'<span class="bb-muted">(bids: {bids})</span></div>')
    for row in summary["players"]:
        parts = [esc(trade_text(row["trade"]))]
        card = row.get("card")
        if card:
            target = ""
            if card.get("target_commodity"):
                target = f' on {esc(theme.commodity_name(card["target_commodity"]))}'
            elif card.get("target_player"):
                target = f' on {esc(names.get(card["target_player"], "?"))}'
            parts.append(f'played {esc(theme.card_label(card["type"]))}{target}')
        if row.get("insider_tip"):
            parts.append(f'used {esc(theme.card_label("insider_tip"))}')
        effects = []
        for e in row["effects"]:
            label = EFFECT_TEXT.get(e["kind"], e["kind"])
            if e["kind"] == "spoiled":
                effects.append(f'{label} 1 {esc(theme.commodity_name(e["commodity"]))}')
                continue
            amount = f'{e["amount"]:+d}' if e["amount"] else (e.get("note") or "0")
            effects.append(f'{label} {esc(amount)}')
        effect_html = f' <span class="bb-muted">({", ".join(effects)})</span>' if effects else ""
        lines.append(f'<div class="bb-log"><span class="who">{esc(row["name"])}</span> {"; ".join(parts)}'
                     f'{effect_html} → <b style="color:var(--bb-teak)">{row["net_worth"]}</b></div>')
    return "".join(lines)


def podium_html(results: list[dict]) -> str:
    top = results[:3]
    order = [1, 0, 2] if len(top) == 3 else list(range(len(top)))
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}
    cols = []
    for i in order:
        r = top[i]
        cols.append(f'<div class="col p{min(i + 1, 3)}"><div>{medal.get(r["rank"], "🏅")}</div>'
                    f'<div class="nm">{esc(r["name"])}</div><div class="blk">{r["net_worth"]}</div></div>')
    return f'<div class="bb-podium">{"".join(cols)}</div>'


def results_table_html(results: list[dict]) -> str:
    """A table on wide screens that turns into stacked cards on phones."""
    with_orders = any(r.get("order") for r in results)
    heads = ["Rank", "Trader", "Net worth", "Coins", "Goods value"] + (["Secret order", "Bonus"] if with_orders else [])
    rows = []
    for r in results:
        vals = [r["rank"], r["name"], r["net_worth"], r["coins"], r["inventory_value"]]
        if with_orders:
            vals += [theme.order_label(r.get("order")), f'+{r.get("order_bonus", 0)}' if r.get("order_bonus") else "—"]
        rows.append("<tr>" + "".join(f'<td data-label="{h}">{esc(v)}</td>' for h, v in zip(heads, vals)) + "</tr>")
    return (f'<table class="bb-table"><thead><tr>{"".join(f"<th>{h}</th>" for h in heads)}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


# ---------------------------------------------------------------------------
# Pure helpers for round feedback (unit-tested)
# ---------------------------------------------------------------------------

def my_round_result(summaries: list[dict], player_id: str, starting_coins: int) -> Optional[dict]:
    """Net-worth change and flags for the player's most recently resolved round."""
    if not summaries:
        return None
    last = summaries[-1]
    row = next((r for r in last["players"] if r["player_id"] == player_id), None)
    if row is None:
        return None
    before = starting_coins
    if len(summaries) >= 2:
        prev = next((r for r in summaries[-2]["players"] if r["player_id"] == player_id), None)
        before = prev["net_worth"] if prev else starting_coins
    caught = any(e["kind"] == "tax_paid" and e.get("note") for e in row["effects"])
    return {"round": last["round"], "event": last["event"], "before": before, "after": row["net_worth"],
            "delta": row["net_worth"] - before, "auto": bool(row["trade"].get("auto")), "caught_thief": caught}


def round_reaction(result: Optional[dict]) -> Optional[str]:
    """At most one reaction per round, and only for notable moments."""
    if not result:
        return None
    if result["caught_thief"]:
        return "caught"
    if result["delta"] >= theme.PROFIT_REACTION_THRESHOLD:
        return "profit"
    return None


# ---------------------------------------------------------------------------
# Full Bazaar panels
# ---------------------------------------------------------------------------

def lots_won(summaries: list[dict], player_id: str) -> int:
    return sum(1 for s in summaries if (s.get("auction") or {}).get("winner") == player_id)


def order_progress(order_key: str, inventory: dict[str, int], coins: int, won: int) -> list[tuple[str, int, int]]:
    """(label, have, need) for each condition of an order."""
    order = ORDERS[order_key]
    parts = [(theme.commodity_name(c), inventory.get(c, 0), n) for c, n in order.get("need", {}).items()]
    if "coins" in order:
        parts.append(("coins", coins, order["coins"]))
    if "lots" in order:
        parts.append(("cargo lots won", won, order["lots"]))
    return parts


def order_html(order_key: Optional[str], inventory: dict[str, int], coins: int, won: int, english: bool) -> str:
    if not order_key:
        return ""
    d = ORDER_DISPLAY[order_key]
    parts = order_progress(order_key, inventory, coins, won)
    done = all(have >= need for _, have, need in parts)
    prog = " · ".join(f'{esc(label)} {min(have, need)}/{need}' for label, have, need in parts)
    status = '<span class="ok">✓ on track</span>' if done else ""
    gloss = f' <span class="bb-en">({esc(d["en"])})</span>' if english else ""
    return (f'<div class="bb-order"><div class="t">📜 Secret order: {d["icon"]} {esc(d["name"])}{gloss}'
            f' · +{ORDERS[order_key]["bonus"]}</div><div class="p">{esc(d["text"])} '
            f'<span class="bb-muted">Progress: {prog}</span> {status}</div>'
            f'<div class="bb-muted">Only you can see this. It is revealed at the end.</div></div>')


def rules_chips_html(config: dict, round_no: int, rules: Optional[dict], prices: dict[str, int],
                     english: bool) -> str:
    """Public Full Bazaar status: the monsoon season and this round's cargo lot."""
    if config.get("rules") != "full":
        return ""
    chips = []
    season = config.get("season")
    if season:
        sd = SEASON_DISPLAY[season]
        start, end = config["season_start"], config["season_end"]
        if start <= round_no <= end:
            chips.append(f'<span class="bb-chip warn">{esc(theme.season_label(season, english))} now: {esc(sd["text"])}</span>')
        elif round_no < start:
            chips.append(f'<span class="bb-chip">{esc(theme.season_label(season, english))} rounds {start}–{end}: '
                         f'{esc(sd["text"])}</span>')
    lot = (rules or {}).get("lot")
    if lot:
        chips.append(f'<span class="bb-chip gold">⛵ {esc(theme.CARGO_TITLE)}: {lot["quantity"]} × '
                     f'{esc(theme.commodity_name(lot["commodity"]))} (worth {lot["quantity"] * prices[lot["commodity"]]}, '
                     f'min bid {lot["reserve"]})</span>')
    return f'<div class="bb-row" style="margin:.1rem 0 .6rem">{"".join(chips)}</div>' if chips else ""


def breakdown_html(b: Optional[dict]) -> str:
    """One line explaining where a round's net-worth change came from (reconciles exactly)."""
    if not b:
        return ""
    parts = [("Price moves", b["price_moves"]), ("Cards & events", b["cards_events"]),
             ("Cargo", b.get("cargo", 0)), ("Spoiled", b.get("spoiled", 0))]
    shown = [f'{label} {v:+d}' for label, v in parts if v]
    trade = f' · trades moved {abs(b["trade_cash"])} coins {"out" if b["trade_cash"] < 0 else "in"}' if b.get("trade_cash") else ""
    return f'<div class="bb-break">{" · ".join(shown) or "No change in value"}{trade}</div>'
