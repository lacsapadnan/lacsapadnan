#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python scripts/build_cards.py            # writes cards/*.svg and refreshes README.md
    STATIC=1 python scripts/build_cards.py   # final frame, no animation -> preview/ (for screenshots)

Everything an <img>-embedded SVG may use, nothing else: an inline <style> block with
CSS keyframes, SMIL <animate>, and Geist subset with pyftsubset into base64 WOFF data URIs.
Data comes from data/contributions.json (refreshed daily by .github/workflows/refresh.yml).

Animation rules
  * Every element's resting style IS its final frame; keyframes only define `from`,
    so turning animation off (reduced motion, STATIC=1) leaves the finished card.
  * SMIL cannot be switched off from CSS, so SMIL is only used for decorative loops
    (class .smil) that are hidden under prefers-reduced-motion.
  * Delays are absolute per card (BASE below), so the page plays top to bottom.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CARDS_DATA") or ROOT / "data" / "contributions.json")
STATIC = os.environ.get("STATIC", "").lower() not in ("", "0", "false", "no")
OUT = ROOT / ("preview" if STATIC else "cards")

# --- brand: read from pascaladnan.com (dark theme) ---------------------------------
BG, SURF, INK = "#0f1110", "#171a18", "#1f2321"
FG, MUTED, LINE = "#ecede9", "#a3a8a2", "#2b2f2c"
ACCENT, BRAND, GREEN = "#ff7b4e", "#c8411a", "#2fb36b"
EASE = "cubic-bezier(.16,1,.3,1)"  # the site's --ease

W = 900                    # every full-width card
TILE_W, TILE_H = W * 0.325, 150   # 32.5% of the stack width, so scale matches the other cards
PAD = 32
SITE = "https://www.pascaladnan.com/en/"
TAGLINE = ("Fullstack developer and founder.", " I build digital products from server to screen.")
STATUS, PLACE, BRAND_LABEL = "AVAILABLE FOR NEW PROJECTS", "JAKARTA, INDONESIA", "PIXELOVERTURE"
TILES = [  # (code, title, caption, href, filled)
    ("W/01", "Work", "6 LIVE CLIENT PRODUCTS", SITE + "#work", False),
    ("B/02", "Blog", "READ ON PASCALADNAN.COM", SITE + "blog/", False),
    ("H/03", "Say hello", "HELLO@PASCALADNAN.COM", "mailto:hello@pascaladnan.com", True),
]
BASE = {"hero": 0.0, "tiles": 0.9, "activity": 1.5, "repos": 2.5, "graph": 3.5}  # seconds

FACES = {  # key -> (css family, weight, file)
    "s4": ("Geist", 400, "Geist-Regular.woff2"),
    "s5": ("Geist", 500, "Geist-Medium.woff2"),
    "s6": ("Geist", 600, "Geist-SemiBold.woff2"),
    "m4": ("Geist Mono", 400, "GeistMono-Regular.woff2"),
}


# --- font metrics + subsetting -----------------------------------------------------
class Metrics:
    def __init__(self, face: str):
        f = TTFont(ROOT / "fonts" / FACES[face][2])
        self.cmap, self.hmtx, self.upem = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm

    def width(self, s: str, size: float, ls: float = 0.0) -> float:
        adv = sum(self.hmtx[self.cmap[ord(c)]][0] for c in s if ord(c) in self.cmap)
        return adv * size / self.upem + ls * len(s)


_METRICS: dict[str, Metrics] = {}


def metrics(face: str) -> Metrics:
    return _METRICS.setdefault(face, Metrics(face))


def clean(s: str, face: str) -> str:
    """Drop glyphs the font lacks (emoji etc.) so measuring and subsetting agree."""
    cm = metrics(face).cmap
    return re.sub(r"\s+", " ", "".join(c for c in s if ord(c) in cm or c == " ")).strip()


def subset_woff_b64(face: str, chars: set[str]) -> str:
    chars = chars | {" "}
    exe = shutil.which("pyftsubset")
    cmd = [exe] if exe else [sys.executable, "-m", "fontTools.subset"]
    with tempfile.TemporaryDirectory() as td:
        txt, out = Path(td, "chars.txt"), Path(td, "out.woff")
        txt.write_text("".join(sorted(chars)), encoding="utf-8")
        subprocess.run(
            cmd + [str(ROOT / "fonts" / FACES[face][2]), f"--text-file={txt}", f"--output-file={out}",
                   "--flavor=woff", "--no-hinting"],
            check=True, capture_output=True,
        )
        return base64.b64encode(out.read_bytes()).decode()


# --- tiny SVG toolkit --------------------------------------------------------------
def r2(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def fmt(n: int) -> str:
    return f"{n:,}"


def mix(a: str, b: str, t: float) -> str:
    pa, pb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


class Card:
    def __init__(self, name: str, w: float, h: float, label: str, base: float = 0.0, extra_css: str = ""):
        self.name, self.w, self.h, self.label, self.base, self.extra_css = name, w, h, label, base, extra_css
        self.body: list[str] = []
        self.defs: list[str] = []
        self.used = {k: set() for k in FACES}

    # text ---------------------------------------------------------------------
    def txt(self, x, y, s, face="s4", size=16.0, fill=FG, anchor="start", ls=0.0) -> str:
        s = clean(s, face)
        self.used[face].update(s)
        fam = "m" if face.startswith("m") else "g"
        return (f'<text x="{r2(x)}" y="{r2(y)}" class="{fam}" font-weight="{FACES[face][1]}" font-size="{r2(size)}" fill="{fill}"'
                + (f' text-anchor="{anchor}"' if anchor != "start" else "")
                + (f' letter-spacing="{r2(ls)}"' if ls else "") + f">{esc(s)}</text>")

    def runs(self, x, y, parts, size, ls=0.0) -> str:
        spans = []
        for s, face, fill in parts:
            s = clean(s, face) if not s.startswith(" ") else " " + clean(s, face)
            self.used[face].update(s)
            spans.append(f'<tspan font-weight="{FACES[face][1]}" fill="{fill}" xml:space="preserve">{esc(s)}</tspan>')
        return (f'<text x="{r2(x)}" y="{r2(y)}" class="g" font-size="{r2(size)}"'
                + (f' letter-spacing="{r2(ls)}"' if ls else "") + f' xml:space="preserve">{"".join(spans)}</text>')

    # motion -------------------------------------------------------------------
    def a(self, inner: str, kind: str = "rise", delay: float = 0.0) -> str:
        if STATIC:
            return inner
        return f'<g class="{kind}" style="animation-delay:{self.base + delay:.2f}s">{inner}</g>'

    def pulse(self, cx, cy, r0, r1, color, begin=1.0, dur=2.4) -> str:
        """SMIL ring; decorative only, hidden under prefers-reduced-motion."""
        if STATIC:
            return ""
        b = f"{self.base + begin:.2f}s"
        return (f'<circle class="smil" cx="{r2(cx)}" cy="{r2(cy)}" r="{r0}" fill="none" stroke="{color}" stroke-width="1.5" opacity="0">'
                f'<animate attributeName="r" values="{r0};{r1}" dur="{dur}s" begin="{b}" repeatCount="indefinite"/>'
                f'<animate attributeName="opacity" values=".7;0" dur="{dur}s" begin="{b}" repeatCount="indefinite"/></circle>')

    # shapes -------------------------------------------------------------------
    def frame(self, x=0.0, w=None, fill=SURF, stroke=LINE) -> str:
        w = self.w - 2 * x if w is None else w
        return f'<rect x="{r2(x + .5)}" y=".5" width="{r2(w - 1)}" height="{r2(self.h - 1)}" rx="18" fill="{fill}" stroke="{stroke}"/>'

    def arrow(self, cx, cy, r=18, color=ACCENT, ring=LINE, delay=0.0) -> str:
        draw = "" if STATIC else f' class="draw" style="animation-delay:{self.base + delay:.2f}s"'
        circ = f'<circle r="{r}" fill="none" stroke="{ring}" stroke-width="1.5" pathLength="1"{draw}/>'
        arr = (f'<path d="M-5 5L5 -5M-1 -5H5V1" fill="none" stroke="{color}" stroke-width="1.8" '
               f'stroke-linecap="round" stroke-linejoin="round"/>')
        return f'<g transform="translate({r2(cx)} {r2(cy)})">{circ}{self.a(arr, "fade", delay + 0.35)}</g>'

    # output -------------------------------------------------------------------
    def css(self) -> str:
        faces = []
        for k, chars in self.used.items():
            if not chars:
                continue
            fam, wt, _ = FACES[k]
            faces.append(f'@font-face{{font-family:"{fam}";font-weight:{wt};font-style:normal;'
                         f'src:url(data:font/woff;base64,{subset_woff_b64(k, chars)}) format("woff")}}')
        css = "".join(faces)
        css += ('.g{font-family:Geist,system-ui,-apple-system,"Segoe UI",sans-serif}'
                '.m{font-family:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,monospace}'
                'text{font-kerning:normal}')
        if not STATIC:
            css += (f"@keyframes rise{{from{{opacity:0;transform:translateY(14px)}}}}"
                    "@keyframes fade{from{opacity:0}}"
                    "@keyframes grow{from{transform:scaleY(0)}}"
                    "@keyframes draw{from{stroke-dashoffset:1}}"
                    ".rise,.fade,.grow,.draw,.slide{animation-fill-mode:both;animation-timing-function:" + EASE + "}"
                    ".rise{animation-name:rise;animation-duration:.9s}"
                    ".fade{animation-name:fade;animation-duration:.8s}"
                    ".grow{animation-name:grow;animation-duration:1s;transform-box:fill-box;transform-origin:50% 100%}"
                    ".draw{animation-name:draw;animation-duration:1.2s;stroke-dasharray:1}"
                    ".slide{animation-duration:1.15s}"
                    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.smil{display:none}}")
        return css + self.extra_css

    def svg(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{r2(self.w)}" height="{r2(self.h)}" '
                f'viewBox="0 0 {r2(self.w)} {r2(self.h)}" role="img" aria-label="{esc(self.label)}">'
                f"<title>{esc(self.label)}</title><style>{self.css()}</style>"
                f"<defs>{''.join(self.defs)}</defs>{''.join(self.body)}</svg>\n")


# --- data --------------------------------------------------------------------------
def load():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    for d in data["days"]:
        d["d"] = dt.date.fromisoformat(d["date"])
    return data


def streaks(days):
    best = run = 0
    for d in days:
        run = run + 1 if d["count"] > 0 else 0
        best = max(best, run)
    i = len(days) - 1
    if days[i]["count"] == 0:  # today not done yet: the streak is still alive
        i -= 1
    cur = 0
    while i >= 0 and days[i]["count"] > 0:
        cur, i = cur + 1, i - 1
    return cur, best


def months(days):
    out: dict[tuple[int, int], dict] = {}
    for d in days:
        m = out.setdefault((d["d"].year, d["d"].month), {"sum": 0, "n": 0, "year": d["d"].year, "month": d["d"].month})
        m["sum"] += d["count"]
        m["n"] += 1
    keys = sorted(out)
    first, last = days[0]["d"], days[-1]["d"]
    end_of_last = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    for k in keys:
        out[k]["partial"] = (k == keys[0] and first.day > 1) or (k == keys[-1] and last != end_of_last)
    return [out[k] for k in keys]


def short_date(d: dt.date) -> str:
    return f"{d:%b} {d.day}"


def wrap(s, face, size, maxw, maxlines, ls=0.0):
    m, s = metrics(face), clean(s, face)
    lines, cur = [], ""
    words = s.split()
    for i, w in enumerate(words):
        t = f"{cur} {w}".strip()
        if m.width(t, size, ls) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
        if len(lines) == maxlines:
            break
    else:
        lines.append(cur)
        return lines
    lines = lines[:maxlines]
    last = lines[-1]
    ell = "…" if ord("…") in m.cmap else "..."
    while last and m.width(last + ell, size, ls) > maxw:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    lines[-1] = last.rstrip(" ,.;:") + ell
    return lines


def load_portrait() -> str | None:
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = ROOT / "assets" / f"portrait.{ext}"
        if p.exists():
            try:
                from PIL import Image

                im = Image.open(p).convert("RGB")
                side = min(im.size)
                l, t = (im.width - side) // 2, (im.height - side) // 3  # bias the crop upward (faces)
                im = im.crop((l, t, l + side, t + side)).resize((288, 288), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=84, optimize=True)
                return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
            except ImportError:
                mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}[ext]
                return f"data:image/{mime};base64," + base64.b64encode(p.read_bytes()).decode()
    return None


# --- cards -------------------------------------------------------------------------
def hero(portrait: str | None) -> Card:
    h = 312
    name = "Pascal Adnan"
    c = Card("hero", W, h, f"{name}. {TAGLINE[0]}{TAGLINE[1]} {STATUS.capitalize()}.", BASE["hero"])
    c.body.append(c.frame(fill=BG))

    # portrait (optional), top right, rounded like the site's cards
    psize = 132
    avail = W - 2 * PAD - (psize + 28 if portrait else 0)
    ls_em = -0.065  # the site's name tracking
    size = min(152.0, 0.97 * avail / metrics("s6").width(name, 1.0, ls_em))
    cap_top = 72
    base_y = cap_top + 0.71 * size
    ox = PAD - 0.045 * size  # optical alignment of the P's side bearing

    c.body.append(c.a(c.txt(PAD, 44, BRAND_LABEL, "m4", 12, MUTED, ls=0.72), "fade", 0.0))

    band_top, band_h = base_y - 0.80 * size, 0.92 * size
    c.defs.append(f'<clipPath id="nm"><rect x="0" y="{r2(band_top)}" width="{W}" height="{r2(band_h)}"/></clipPath>')
    nm = c.txt(ox, base_y, name, "s6", size, FG, ls=ls_em * size)
    if STATIC:
        c.body.append(f'<g clip-path="url(#nm)">{nm}</g>')
    else:
        c.extra_css += f"@keyframes slide{{from{{transform:translateY({r2(size)}px)}}}}.slide{{animation-name:slide}}"
        c.body.append(f'<g clip-path="url(#nm)"><g class="slide" style="animation-delay:{c.base + .15:.2f}s">{nm}</g></g>')

    if portrait:
        px, py = W - PAD - psize, 32
        c.defs.append(f'<clipPath id="pt"><rect x="{px}" y="{py}" width="{psize}" height="{psize}" rx="24"/></clipPath>')
        img = (f'<image href="{portrait}" x="{px}" y="{py}" width="{psize}" height="{psize}" clip-path="url(#pt)" '
               f'preserveAspectRatio="xMidYMid slice"/><rect x="{px + .5}" y="{py + .5}" width="{psize - 1}" height="{psize - 1}" '
               f'rx="24" fill="none" stroke="{LINE}"/>')
        c.body.append(c.a(img, "rise", 0.35))

    # one line about me: bold first sentence, muted second (as on the site)
    tsize = 20.0
    tag_w = lambda s: metrics("s6").width(TAGLINE[0], s, -0.01 * s) + metrics("s4").width(TAGLINE[1], s, -0.01 * s)
    while tag_w(tsize) > (W - 2 * PAD) * 0.98 and tsize > 14:
        tsize -= 0.5
    ty = base_y + 46
    c.body.append(c.a(c.runs(PAD, ty, [(TAGLINE[0], "s6", FG), (TAGLINE[1], "s4", MUTED)], tsize, -0.01 * tsize), "rise", 0.55))

    # footer row, like the site's
    sep_y, row_y = h - 62, h - 28
    c.body.append(c.a(f'<path d="M{PAD} {sep_y}H{W - PAD}" stroke="{LINE}"/>', "fade", 0.7))
    dot = (f'<circle cx="{PAD + 4}" cy="{row_y - 4}" r="4" fill="{GREEN}"/>' + c.pulse(PAD + 4, row_y - 4, 4, 10, GREEN, 1.2))
    row = dot + c.txt(PAD + 18, row_y, STATUS, "m4", 12, FG, ls=0.72) + c.txt(W - PAD, row_y, PLACE, "m4", 12, MUTED, "end", 0.72)
    c.body.append(c.a(row, "fade", 0.8))
    return c


def tile(i: int) -> Card:
    code, title, caption, _href, filled = TILES[i]
    inset = 5  # 3 x 32.5% leaves 2.5%, so give each tile a little air on both sides
    c = Card(f"tile-{title.lower().replace(' ', '-')}", TILE_W, TILE_H, f"{title}: {caption.capitalize()}", BASE["tiles"] + i * 0.14)
    x0, w = inset, TILE_W - 2 * inset
    fg, sub, accent, ring = (("#ffffff",) * 3 + ("#ffffff",)) if filled else (FG, MUTED, ACCENT, LINE)
    c.body.append(c.frame(inset, fill=BRAND if filled else SURF, stroke=BRAND if filled else LINE))
    pad = 22
    c.body.append(c.a(c.txt(x0 + pad, 40, code, "m4", 12, accent, ls=0.72), "fade", 0.1))
    c.body.append(c.a(c.txt(x0 + pad, 100, title, "s6", 30, fg, ls=-0.9), "rise", 0.15))
    c.body.append(c.a(c.txt(x0 + pad, 126, caption, "m4", 12, sub, ls=0.72), "rise", 0.3))
    c.body.append(c.arrow(x0 + w - pad - 16, 38, 16, "#ffffff" if filled else ACCENT, "#ffffff" if filled else LINE, 0.25))
    return c


def activity(data) -> Card:
    days = data["days"]
    ms = months(days)
    cur, best = streaks(days)
    active = sum(1 for d in days if d["count"] > 0)
    top = max(days, key=lambda d: d["count"])
    total = sum(d["count"] for d in days)
    h = 392
    span = f"{days[0]['d']:%b %Y} – {days[-1]['d']:%b %Y}".upper()
    c = Card("activity", W, h, (f"{fmt(total)} contributions in the last year, {span.title()}. Current streak {cur} days, longest {best}, "
                                f"{active} active days of {len(days)}, busiest day {top['count']} on {short_date(top['d'])}."), BASE["activity"])
    c.body.append(c.frame())
    c.body.append(c.a(c.txt(PAD, 44, "CONTRIBUTIONS PER MONTH", "m4", 12, MUTED, ls=0.72), "fade", 0.0))

    # left column: headline number + stat rows
    c.body.append(c.a(c.txt(PAD - 2, 132, fmt(total), "s6", 68, FG, ls=-2.7), "rise", 0.1))
    c.body.append(c.a(c.txt(PAD, 164, "CONTRIBUTIONS", "m4", 12, MUTED, ls=0.72) + c.txt(PAD, 182, span, "m4", 12, MUTED, ls=0.72), "rise", 0.2))
    rows = [("CURRENT STREAK", fmt(cur), "DAY" if cur == 1 else "DAYS"),
            ("LONGEST STREAK", fmt(best), "DAYS"),
            ("ACTIVE DAYS", fmt(active), f"OF {len(days)}"),
            ("BUSIEST DAY", fmt(top["count"]), short_date(top["d"]).upper())]
    col_r, y0, pitch = 252, 204, 46
    for i, (lab, val, unit) in enumerate(rows):
        y = y0 + i * pitch
        uw = metrics("m4").width(unit, 12, 0.72)
        inner = (f'<path d="M{PAD} {y}H{col_r}" stroke="{LINE}"/>' + c.txt(PAD, y + 28, lab, "m4", 11, MUTED, ls=0.66)
                 + c.txt(col_r - uw - 8, y + 31, val, "s6", 24, FG, "end", -0.5) + c.txt(col_r, y + 29, unit, "m4", 12, MUTED, "end", 0.72))
        c.body.append(c.a(inner, "rise", 0.3 + i * 0.1))

    # chart
    x0, x1, top_y, base_y = 312, W - PAD, 92, 332
    vmax = max(m["sum"] for m in ms) or 1
    step = next(s for s in (50, 100, 200, 250, 500, 1000, 2000, 5000) if math.ceil(vmax / s) <= 5)
    ymax = math.ceil(vmax / step) * step
    sy = lambda v: base_y - (base_y - top_y) * v / ymax
    grid = ""
    for v in range(0, ymax + 1, step):
        dash = "" if v == 0 else ' stroke-dasharray="2 4"'
        grid += f'<path d="M{x0 - 8} {r2(sy(v))}H{x1}" stroke="{LINE}" stroke-width="1"{dash}/>'
        grid += c.txt(x0 - 16, sy(v) + 4, fmt(v), "m4", 11, MUTED, "end")
    c.body.append(c.a(grid, "fade", 0.1))

    slot = (x1 - x0) / len(ms)
    bw = min(26.0, slot - 14)
    peak = max(range(len(ms)), key=lambda i: ms[i]["sum"])
    for i, m in enumerate(ms):
        cx = x0 + slot * (i + .5)
        bx, hgt = cx - bw / 2, (base_y - sy(m["sum"]))
        if hgt >= 1:
            r = min(4.0, hgt / 2, bw / 2)
            path = (f"M{r2(bx)} {base_y}V{r2(base_y - hgt + r)}a{r2(r)} {r2(r)} 0 0 1 {r2(r)} {r2(-r)}H{r2(bx + bw - r)}"
                    f"a{r2(r)} {r2(r)} 0 0 1 {r2(r)} {r2(r)}V{base_y}Z")
            style = (f'fill="{ACCENT}" fill-opacity=".16" stroke="{ACCENT}" stroke-width="1.5" stroke-dasharray="3 3"'
                     if m["partial"] else f'fill="{ACCENT}"')
            bar = f'<path d="{path}" {style}/>'
            bar = bar if STATIC else f'<g class="grow" style="animation-delay:{c.base + .25 + i * .06:.2f}s">{bar}</g>'
            c.body.append(bar)
        label = dt.date(m["year"], m["month"], 1)
        lab = c.txt(cx, base_y + 22, f"{label:%b}".upper(), "m4", 11, MUTED, "middle", 0.66)
        if i == 0 or label.month == 1:
            lab += c.txt(cx, base_y + 38, str(label.year), "m4", 11, MUTED, "middle", 0.66)
        c.body.append(c.a(lab, "fade", 0.3 + i * 0.04))
        if i in (peak, len(ms) - 1):  # selective direct labels only
            c.body.append(c.a(c.txt(cx, sy(m["sum"]) - 9, fmt(m["sum"]), "s5", 13, FG, "middle"), "fade", 0.9 + i * 0.03))

    # legend (top right)
    lx = x1
    for text, partial in (("PARTIAL MONTH", True), ("FULL MONTH", False)):
        tw = metrics("m4").width(text, 11, 0.66)
        sw = (f'<rect x="{r2(lx - tw - 20)}" y="35" width="10" height="10" rx="2.5" fill="{ACCENT}" fill-opacity=".16" '
              f'stroke="{ACCENT}" stroke-dasharray="2 2"/>' if partial else
              f'<rect x="{r2(lx - tw - 20)}" y="35" width="10" height="10" rx="2.5" fill="{ACCENT}"/>')
        c.body.append(c.a(sw + c.txt(lx, 44, text, "m4", 11, MUTED, "end", 0.66), "fade", 0.2))
        lx -= tw + 20 + 22
    return c


def repo_card(i: int, n: int, r: dict) -> Card:
    h = 160
    c = Card(f"repo-{i + 1}", W, h, f"{r['name']}: {r.get('description') or 'repository'}"
             f"{' ' + r['language'] if r.get('language') else ''}, {r.get('stars', 0)} stars, {r.get('forks', 0)} forks",
             BASE["repos"] + i * 0.22)
    c.body.append(c.frame())
    c.body.append(c.a(c.txt(PAD, 54, r["name"], "s6", 24, FG, ls=-0.72), "rise", 0.0))
    c.body.append(c.arrow(W - PAD - 18, 46, 18, ACCENT, LINE, 0.1))
    desc = wrap(r.get("description") or "", "s4", 15, 700, 2) if r.get("description") else []
    for k, line in enumerate(desc):
        c.body.append(c.a(c.txt(PAD, 84 + k * 22, line, "s4", 15, MUTED), "rise", 0.08 + k * 0.05))
    # meta row: language, stars, forks (zero counts stay out of the way)
    y, x = h - 26, PAD
    meta = ""
    if r.get("language"):
        meta += f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{r.get("color") or MUTED}"/>'
        meta += c.txt(x + 18, y, r["language"].upper(), "m4", 12, FG, ls=0.72)
        x += 18 + metrics("m4").width(r["language"].upper(), 12, 0.72) + 28
    if r.get("stars"):
        meta += (f'<path transform="translate({x} {y - 12}) scale(.95)" fill="{MUTED}" d="M7 .4l1.9 4.3 4.7.5-3.5 3.1 1 4.6L7 10.6 2.9 12.9l1-4.6L.4 5.2l4.7-.5z"/>')
        meta += c.txt(x + 20, y, fmt(r["stars"]), "m4", 12, MUTED, ls=0.72)
        x += 20 + metrics("m4").width(fmt(r["stars"]), 12, 0.72) + 24
    if r.get("forks"):
        meta += (f'<g transform="translate({x} {y - 13})" fill="none" stroke="{MUTED}" stroke-width="1.5" stroke-linecap="round">'
                 '<circle cx="3.5" cy="2.8" r="1.8"/><circle cx="10.5" cy="2.8" r="1.8"/><circle cx="7" cy="12" r="1.8"/>'
                 '<path d="M3.5 4.6V6c0 1 .8 1.8 1.8 1.8h3.4c1 0 1.8-.8 1.8-1.8V4.6M7 7.8v2.4"/></g>')
        meta += c.txt(x + 20, y, fmt(r["forks"]), "m4", 12, MUTED, ls=0.72)
    c.body.append(c.a(meta, "fade", 0.3))
    return c


def graph(data) -> Card:
    days = data["days"]
    total = sum(d["count"] for d in days)
    start = (days[0]["d"].weekday() + 1) % 7  # GitHub weeks start on Sunday
    cols = math.ceil((start + len(days)) / 7)
    gx0, gy0 = PAD + 34, 98
    pitch = (W - PAD - gx0) / cols
    cell = pitch - 3
    h = int(gy0 + 7 * pitch + 62)
    c = Card("graph", W, h, f"Contribution graph: {fmt(total)} contributions in the last year", BASE["graph"])
    c.body.append(c.frame())
    c.body.append(c.a(c.txt(PAD, 44, "CONTRIBUTION GRAPH", "m4", 12, MUTED, ls=0.72)
                      + c.txt(W - PAD, 44, f"{fmt(total)} IN THE LAST YEAR", "m4", 12, MUTED, "end", 0.72), "fade", 0.0))
    ramp = [INK] + [mix(INK, ACCENT, t) for t in (0.34, 0.58, 0.8, 1.0)]

    # month labels
    last_col, last_month = -9, None
    labels = ""
    for col in range(cols):
        first = days[max(0, col * 7 - start)]["d"]
        if first.month != last_month and col - last_col >= 3:
            labels += c.txt(gx0 + col * pitch, gy0 - 14, f"{first:%b}".upper(), "m4", 11, MUTED, ls=0.66)
            last_col = col
        last_month = first.month
    c.body.append(c.a(labels, "fade", 0.15))
    wd = "".join(c.txt(PAD, gy0 + (row + .5) * pitch + 4, name, "m4", 11, MUTED, ls=0.66) for row, name in ((1, "MON"), (3, "WED"), (5, "FRI")))
    c.body.append(c.a(wd, "fade", 0.15))

    # cells, one animated group per week so the graph sweeps in left to right
    for col in range(cols):
        rects = ""
        for row in range(7):
            idx = col * 7 + row - start
            if 0 <= idx < len(days):
                rects += (f'<rect x="{r2(gx0 + col * pitch)}" y="{r2(gy0 + row * pitch)}" width="{r2(cell)}" height="{r2(cell)}" '
                          f'rx="3" fill="{ramp[days[idx]["level"]]}"/>')
        c.body.append(c.a(rects, "fade", 0.2 + col * 0.022))
    # today marker (decorative SMIL)
    li = len(days) - 1 + start
    tx, ty = gx0 + (li // 7) * pitch, gy0 + (li % 7) * pitch
    if not STATIC:
        c.body.append(f'<rect class="smil" x="{r2(tx - 1.5)}" y="{r2(ty - 1.5)}" width="{r2(cell + 3)}" height="{r2(cell + 3)}" rx="4.5" fill="none" '
                      f'stroke="{FG}" stroke-width="1.5" opacity="0"><animate attributeName="opacity" values="0;.9;0" dur="2.4s" '
                      f'begin="{c.base + 1.8:.2f}s" repeatCount="indefinite"/></rect>')

    # legend
    ly = h - 28
    lg = c.txt(W - PAD, ly + 9, "MORE", "m4", 11, MUTED, "end", 0.66)
    mw = metrics("m4").width("MORE", 11, 0.66)
    x = W - PAD - mw - 10
    for lvl in range(4, -1, -1):
        lg += f'<rect x="{r2(x - 12)}" y="{ly - 1}" width="12" height="12" rx="3" fill="{ramp[lvl]}"/>'
        x -= 16
    lg += c.txt(x - 4, ly + 9, "LESS", "m4", 11, MUTED, "end", 0.66)
    c.body.append(c.a(lg, "fade", 1.4))
    return c


# --- README ------------------------------------------------------------------------
def stack_html(prefix: str, repos: list[dict]) -> str:
    row = lambda inner: f'<p align="center">{inner}</p>'
    out = [row(f'<a href="{SITE}"><img src="{prefix}hero.svg" alt="Pascal Adnan, fullstack developer and founder" width="100%"></a>')]
    # the three tiles share one line with no whitespace between them: 3 x 32.5% always fits
    out.append(row("".join(f'<a href="{href}"><img src="{prefix}tile-{title.lower().replace(" ", "-")}.svg" alt="{title}" width="32.5%"></a>'
                           for _c, title, _cap, href, _f in TILES)))
    out.append(row(f'<img src="{prefix}activity.svg" alt="Contributions per month, streak, active days and busiest day" width="100%">'))
    for i, r in enumerate(repos):
        out.append(row(f'<a href="{r["url"]}"><img src="{prefix}repo-{i + 1}.svg" alt="{esc(r["name"])} repository" width="100%"></a>'))
    out.append(row(f'<img src="{prefix}graph.svg" alt="Contribution graph for the last year" width="100%">'))
    return "\n\n".join(out)


def write_readme(repos):
    p = ROOT / "README.md"
    block = f"<!--cards:start-->\n{stack_html('cards/', repos)}\n<!--cards:end-->"
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    if "<!--cards:start-->" in old:
        new = re.sub(r"<!--cards:start-->.*<!--cards:end-->", lambda _m: block, old, flags=re.S)
    else:
        print("README.md has no <!--cards:start--> marker; leaving it alone", file=sys.stderr)
        return
    if new != old:
        p.write_text(new, encoding="utf-8")


def write_preview(repos, prefix, name):
    page = ('<!doctype html><meta charset="utf-8"><title>README preview</title>'
            "<style>body{margin:0;background:#0d1117;color:#e6edf3;font:16px/1.5 system-ui}"
            "main{width:888px;margin:0 auto;padding:32px 0}p{margin:0 0 16px}</style>"
            f"<main>{stack_html(prefix, repos)}</main>")
    (ROOT / "preview").mkdir(exist_ok=True)
    (ROOT / "preview" / name).write_text(page, encoding="utf-8")


def main():
    data = load()
    repos = data.get("repos", [])[:6]
    OUT.mkdir(exist_ok=True)
    cards = [hero(load_portrait())] + [tile(i) for i in range(len(TILES))] + [activity(data)] \
        + [repo_card(i, len(repos), r) for i, r in enumerate(repos)] + [graph(data)]
    for card in cards:
        svg = card.svg()
        (OUT / f"{card.name}.svg").write_text(svg, encoding="utf-8")
        print(f"{OUT.name}/{card.name}.svg  {len(svg) / 1024:.1f} KB")
    if STATIC:
        write_preview(repos, "", "index.html")
        print("static preview -> preview/index.html")
    else:
        write_readme(repos)
        write_preview(repos, "../cards/", "animated.html")  # open this to watch the real animation


if __name__ == "__main__":
    main()
