"""Builds the Balsamiq Sans Neue family from the original Balsamiq Sans sources.

Pipeline, per style:
  1. Scale glyphs (SX1 x SY1) to bring the optical size and horizontal
     rhythm in line with classic grotesque text faces.
  2. Thin every stroke by eroding outlines D units per edge (round caps
     and joins keep the rounded hand-drawn terminals), then rescale so
     the cap height lands on TARGET_CAP.
  3. Keep kerning, mark anchors, sidebearings, underline/strikeout and
     OS/2 metrics in sync; strip stale TrueType hints; rename family.

Usage:
  python -m venv .venv && .venv/bin/pip install fonttools skia-pathops
  .venv/bin/python scripts/build.py
"""

from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import ttfGlyphFromSkPath
from fontTools.pens.boundsPen import BoundsPen
import pathops, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "source")
OUT_DIR = os.path.join(ROOT, "fonts")
FAMILY = "Balsamiq Sans Neue"

STYLES = [
    ("BalsamiqSans-Regular.ttf", "BalsamiqSansNeue-Regular.ttf", "Regular"),
    ("BalsamiqSans-Bold.ttf", "BalsamiqSansNeue-Bold.ttf", "Bold"),
    ("BalsamiqSans-Italic.ttf", "BalsamiqSansNeue-Italic.ttf", "Italic"),
    ("BalsamiqSans-BoldItalic.ttf", "BalsamiqSansNeue-BoldItalic.ttf", "Bold Italic"),
]

SX1, SY1 = 0.915, 0.933   # stage 1: optical size + width
D = 4.5                   # stage 2: ink erosion per edge (units)
TARGET_CAP = 668          # final cap height (per 1000 upm)


DEBRIS = 6  # contours smaller than this many units are erosion debris


def signed_area(path):
    total = 0.0
    for c in path.contours:
        total += -c.area if c.clockwise else c.area
    return total


def drop_debris(path):
    clean = pathops.Path()
    pen = clean.getPen()
    for contour in path.contours:
        x0, y0, x1, y1 = contour.bounds
        if x1 - x0 >= DEBRIS or y1 - y0 >= DEBRIS:
            contour.draw(pen)
    return clean


def erode(skpath):
    """Thin strokes by D units per edge. The Skia stroker occasionally
    mis-winds the inner ring of its annulus on degenerate hand-drawn input,
    so try both winding interpretations of the stroke and keep whichever
    candidate removes a sane amount of ink. Falls back to the unthinned
    outline (correct shape, original weight) if every strategy fails."""
    fill = pathops.Path(skpath)
    fill.simplify(clockwise=fill.clockwise)
    fill_area = abs(signed_area(fill))

    best, best_score = None, None
    for fill_type in (pathops.FillType.WINDING, pathops.FillType.EVEN_ODD, None):
        try:
            stroked = pathops.Path(fill)
            stroked.stroke(2 * D, pathops.LineCap.ROUND_CAP, pathops.LineJoin.ROUND_JOIN, 4.0)
            stroked.convertConicsToQuads(0.25)
            if fill_type is not None:
                stroked.fillType = fill_type
                stroked.simplify(clockwise=stroked.clockwise)
            out = pathops.op(fill, stroked, pathops.PathOp.DIFFERENCE)
            out.simplify(clockwise=fill.clockwise)
            out.convertConicsToQuads(0.25)
        except pathops.PathOpsError:
            continue
        if fill_area > 0:
            ratio = abs(signed_area(out)) / fill_area
            if not (0.5 < ratio < 1.0):
                continue
            score = abs(ratio - 0.9)
        else:
            score = 0
        if best is None or score < best_score:
            best, best_score = out, score

    if best is None:
        return drop_debris(fill), False
    return drop_debris(best), True


def build(src, out, subfamily):
    f = TTFont(src)
    glyf = f["glyf"]
    hmtx = f["hmtx"]
    cmap = f.getBestCmap()

    bp = BoundsPen(f.getGlyphSet())
    f.getGlyphSet()[cmap[ord("H")]].draw(bp)
    cap_after_s1 = bp.bounds[3] * SY1
    s2 = TARGET_CAP / (cap_after_s1 - 2 * D)
    tx2 = -D * s2
    sx, sy = SX1 * s2, SY1 * s2

    unthinned = []
    for name in f.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours > 0:
            sk = pathops.Path()
            g.draw(sk.getPen(), glyf)
            sk = sk.transform(SX1, 0, 0, SY1, 0, 0)
            eroded, thinned_ok = erode(sk)
            if not thinned_ok:
                unthinned.append(name)
            glyf[name] = ttfGlyphFromSkPath(eroded.transform(s2, 0, 0, s2, tx2, 0))
        elif g.isComposite():
            for comp in g.components:
                comp.x = round(comp.x * sx)
                comp.y = round(comp.y * sy)
        aw, lsb = hmtx[name]
        new_aw = max(0, round(aw * sx) - round(2 * D * s2)) if aw > 0 else 0
        hmtx[name] = (new_aw, lsb)

    for t in ("fpgm", "prep", "cvt "):
        if t in f:
            del f[t]
    if "gasp" in f:
        f["gasp"].gaspRange = {0xFFFF: 15}

    seen = set()

    def walk(obj):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        if isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)
            return
        if not hasattr(obj, "__dict__"):
            return
        for k, v in vars(obj).items():
            if v is None:
                continue
            if k in ("XPlacement", "XCoordinate") and isinstance(v, (int, float)):
                setattr(obj, k, round(v * sx + tx2))
            elif k == "XAdvance" and isinstance(v, (int, float)):
                setattr(obj, k, round(v * sx))
            elif k in ("YPlacement", "YAdvance", "YCoordinate") and isinstance(v, (int, float)):
                setattr(obj, k, round(v * sy))
            else:
                walk(v)

    for t in ("GPOS", "GDEF"):
        if t in f:
            walk(f[t].table)

    os2 = f["OS/2"]
    os2.sxHeight = round(os2.sxHeight * sy)
    os2.sCapHeight = round(os2.sCapHeight * sy)
    for a in ("ySubscriptXSize", "ySuperscriptXSize"):
        setattr(os2, a, round(getattr(os2, a) * sx))
    for a in ("ySubscriptYSize", "ySubscriptYOffset", "ySuperscriptYSize", "ySuperscriptYOffset"):
        setattr(os2, a, round(getattr(os2, a) * sy))
    os2.yStrikeoutSize = max(20, round(os2.yStrikeoutSize * sy - 2 * D * s2))
    os2.yStrikeoutPosition = round(os2.yStrikeoutPosition * sy)

    post = f["post"]
    post.underlineThickness = max(20, round(post.underlineThickness * sy - 2 * D * s2))
    post.underlinePosition = round(post.underlinePosition * sy)

    order = f.getGlyphOrder()
    widths = [hmtx[n][0] for n in order if hmtx[n][0] > 0]
    os2.xAvgCharWidth = round(sum(widths) / len(widths))

    ps = "BalsamiqSansNeue-" + subfamily.replace(" ", "")
    name = f["name"]
    full = f"{FAMILY} {subfamily}"
    for nid, val in ((1, FAMILY), (2, subfamily), (3, f"1.020;mods;{ps}"),
                     (4, full), (6, ps), (16, FAMILY), (17, subfamily)):
        name.setName(val, nid, 3, 1, 0x409)
    old0 = name.getDebugName(0)
    name.setName(old0 + " Modified version (scaled metrics, adjusted weight), not endorsed by the original authors.", 0, 3, 1, 0x409)

    f.save(out)

    # second pass: sync hmtx lsb with recalculated bounds, fix win metrics
    f2 = TTFont(out)
    gs = f2.getGlyphSet()
    hmtx2 = f2["hmtx"]
    for n in f2.getGlyphOrder():
        bp = BoundsPen(gs)
        gs[n].draw(bp)
        if bp.bounds:
            aw, _ = hmtx2[n]
            hmtx2[n] = (aw, math.floor(bp.bounds[0]))
    head = f2["head"]
    f2["OS/2"].usWinAscent = max(head.yMax, f2["hhea"].ascent)
    f2["OS/2"].usWinDescent = max(-head.yMin, -f2["hhea"].descent)
    f2.save(out)
    return unthinned


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    for src_name, out_name, subfamily in STYLES:
        unthinned = build(os.path.join(SRC_DIR, src_name), os.path.join(OUT_DIR, out_name), subfamily)
        status = "ok" if not unthinned else f"{len(unthinned)} glyphs kept original weight: {unthinned[:5]}"
        print(f"{subfamily:12s} -> {out_name}  [{status}]")
