"""Generate icon_source.png — AIDE multi-agent logo."""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SIZE = 1024
OUT  = "/Users/itay.glick/Documents/GAI Apps/nanoai/icon_source.png"

# ── palette ──────────────────────────────────────────────────────────────────
BG        = (10,  12,  20)
TERM_BG   = (17,  17,  27)
TBAR_BG   = (30,  31,  46)
SURFACE   = (55,  57,  78)
AGENT_A   = (74,  158, 255)     # blue
AGENT_B   = (203, 166, 247)     # purple
AGENT_C   = (166, 227, 161)     # green
WIRE_COL  = (90,  95,  130)
WHITE     = (255, 255, 255)

def rr(draw, box, r, fill):
    draw.rounded_rectangle(list(box), radius=r, fill=fill)

def ellipse(draw, cx, cy, rx, ry, fill):
    draw.ellipse([cx-rx, cy-ry, cx+rx, cy+ry], fill=fill)

def glow(base_img, cx, cy, rx, ry, color, alpha=70, blur=40):
    layer = Image.new('RGBA', base_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx-rx, cy-ry, cx+rx, cy+ry], fill=color[:3] + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=blur))
    base_img.alpha_composite(layer)

def draw_robot(draw, cx, cy, col, scale=1.0):
    """Robotic agent icon: square head, antenna, rectangular eyes."""
    s  = int(28 * scale)   # head half-size
    r  = int(6  * scale)   # corner radius

    # antenna base
    ant_h = int(20 * scale)
    ant_w = int(4  * scale)
    draw.rectangle([cx - ant_w//2, cy - s - ant_h, cx + ant_w//2, cy - s], fill=col)
    # antenna ball
    ellipse(draw, cx, cy - s - ant_h, int(7*scale), int(7*scale), col)

    # head box
    rr(draw, [cx-s, cy-s, cx+s, cy+s], r=r, fill=col + (220,))

    # eyes — two small rectangles
    ew = int(9  * scale)
    eh = int(7  * scale)
    ex = int(10 * scale)
    ey = int(4  * scale)
    rr(draw, [cx-ex-ew//2, cy-ey-eh//2, cx-ex+ew//2, cy-ey+eh//2], r=2, fill=TERM_BG)
    rr(draw, [cx+ex-ew//2, cy-ey-eh//2, cx+ex+ew//2, cy-ey+eh//2], r=2, fill=TERM_BG)

    # mouth bar
    mw = int(18 * scale)
    mh = int(4  * scale)
    my = int(10 * scale)
    rr(draw, [cx-mw//2, cy+my, cx+mw//2, cy+my+mh], r=1, fill=TERM_BG)

def draw_prompt(draw, cx, cy, color, scale=1.0):
    """Draw '> |' cursor glyph centred at (cx, cy)."""
    s   = int(46 * scale)
    sw  = int(11 * scale)
    tip_x = cx + s // 2
    arm_x = cx - s // 2

    draw.line([(arm_x, cy - s), (tip_x, cy)], fill=color, width=sw,
              joint='curve')
    draw.line([(tip_x, cy), (arm_x, cy + s)], fill=color, width=sw,
              joint='curve')

    # round the tip and the start points
    r = sw // 2
    ellipse(draw, tip_x, cy, r, r, color)
    ellipse(draw, arm_x, cy - s, r, r, color)
    ellipse(draw, arm_x, cy + s, r, r, color)

    # cursor bar — blinking block style
    bar_x = tip_x + int(16 * scale)
    bar_h = int(58 * scale)
    bar_w = int(11 * scale)
    draw.rectangle([bar_x, cy - bar_h//2, bar_x + bar_w, cy + bar_h//2], fill=color)

def draw_code_lines(draw, px1, py1, px2, py2, col, seed=0):
    """Faint code lines in the background of a pane."""
    import random
    rng = random.Random(seed)
    line_h = 22
    margin  = 24
    y = py1 + 30
    while y < py2 - 20:
        w = rng.randint(int((px2-px1) * 0.18), int((px2-px1) * 0.72))
        x = px1 + margin
        alpha = rng.randint(22, 48)
        draw.rectangle([x, y, x+w, y+3], fill=col + (alpha,))
        # occasionally indent
        if rng.random() < 0.3:
            w2 = rng.randint(int(w * 0.3), int(w * 0.8))
            draw.rectangle([x+28, y+line_h, x+28+w2, y+line_h+3], fill=col + (alpha,))
            y += line_h
        y += line_h + rng.randint(2, 8)

# ── canvas ───────────────────────────────────────────────────────────────────
img  = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))

# outer background with subtle gradient
rr(ImageDraw.Draw(img), [0, 0, SIZE, SIZE], r=190, fill=BG)

# ── terminal window ───────────────────────────────────────────────────────────
TX1, TY1, TX2, TY2 = 54, 82, 970, 798
TR  = 24
TH  = 58

draw = ImageDraw.Draw(img)
rr(draw, [TX1, TY1, TX2, TY2], r=TR, fill=TERM_BG)

# title bar
rr(draw, [TX1, TY1, TX2, TY1+TH], r=TR, fill=TBAR_BG)
draw.rectangle([TX1, TY1+TR, TX2, TY1+TH], fill=TBAR_BG)

# traffic lights
LY = TY1 + TH // 2
for i, lc in enumerate([(255,95,87), (255,188,46), (39,201,63)]):
    LX = TX1 + 34 + i * 40
    ellipse(draw, LX, LY, 13, 13, lc)
    ellipse(draw, LX-4, LY-5, 4, 4, (255, 255, 255, 70))

# ── three panes ───────────────────────────────────────────────────────────────
BY1 = TY1 + TH
BY2 = TY2
BW  = TX2 - TX1
PW  = BW // 3

div1 = TX1 + PW
div2 = TX1 + PW * 2

pane_bounds = [
    (TX1+1, BY1, div1-1, BY2),
    (div1+2, BY1, div2-1, BY2),
    (div2+2, BY1, TX2-1, BY2),
]
agent_colors = [AGENT_A, AGENT_B, AGENT_C]

# code lines per pane
for (px1, py1, px2, py2), col, seed in zip(pane_bounds, agent_colors, [1, 2, 3]):
    lay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    ld  = ImageDraw.Draw(lay)
    draw_code_lines(ld, px1, py1, px2, py2, col, seed=seed)
    img.alpha_composite(lay)

draw = ImageDraw.Draw(img)

# dividers
draw.rectangle([div1, BY1, div1+2, BY2], fill=SURFACE)
draw.rectangle([div2, BY1, div2+2, BY2], fill=SURFACE)

# ── neural bus wire ───────────────────────────────────────────────────────────
wire_y  = BY1 + (BY2 - BY1) * 34 // 100
wire_x1 = TX1 + 28
wire_x2 = TX2 - 28

# glow behind the wire
glow(img, (wire_x1+wire_x2)//2, wire_y, (wire_x2-wire_x1)//2, 12,
     (160, 130, 220), alpha=50, blur=14)

draw = ImageDraw.Draw(img)
draw.line([(wire_x1, wire_y), (wire_x2, wire_y)], fill=WIRE_COL, width=3)

# connection tap points (one per pane, plus junctions at dividers)
centres_x = [
    TX1 + PW // 2,
    TX1 + PW + PW // 2,
    TX1 + 2*PW + PW // 2,
]
tap_r = 8
junc_r = 11

# vertical drop lines from wire to content
for cx in centres_x:
    draw.line([(cx, wire_y + tap_r), (cx, wire_y + 40)], fill=WIRE_COL, width=2)

# junction nodes at dividers
for jx in [div1+1, div2+1]:
    ellipse(draw, jx, wire_y, junc_r, junc_r, SURFACE)
    ellipse(draw, jx, wire_y, junc_r-4, junc_r-4, TERM_BG)
    ellipse(draw, jx, wire_y, 3, 3, (180, 160, 240))

# pane tap nodes with agent colour
for cx, col in zip(centres_x, agent_colors):
    glow(img, cx, wire_y, 28, 28, col, alpha=80, blur=16)
    draw = ImageDraw.Draw(img)
    ellipse(draw, cx, wire_y, tap_r, tap_r, col)
    ellipse(draw, cx, wire_y, tap_r-3, tap_r-3, TERM_BG)
    ellipse(draw, cx, wire_y, 3, 3, col)

# ── agent icons + prompts ─────────────────────────────────────────────────────
cursor_y = BY1 + (BY2 - BY1) * 65 // 100

for cx, col in zip(centres_x, agent_colors):
    # glow behind cursor
    glow(img, cx, cursor_y, 130, 90, col, alpha=45, blur=50)

    # robot icon sits between the wire and the cursor
    icon_y = BY1 + (BY2 - BY1) * 50 // 100
    glow(img, cx, icon_y, 48, 48, col, alpha=55, blur=20)

    draw = ImageDraw.Draw(img)
    draw_robot(draw, cx, icon_y, col[:3], scale=1.15)
    draw_prompt(draw, cx - 16, cursor_y, col[:3], scale=1.2)

# ── "AIDE" label ─────────────────────────────────────────────────────────────
label_y = 842
try:
    # HelveticaNeue index: 0=regular, 3=bold (varies); try a few
    fnt = ImageFont.truetype("/System/Library/Fonts/HelveticaNeue.ttc",
                             size=90, index=1)   # index 1 = Bold
except Exception:
    fnt = ImageFont.load_default()

draw = ImageDraw.Draw(img)
bbox = draw.textbbox((0, 0), "AIDE", font=fnt)
tw   = bbox[2] - bbox[0]
# slight glow behind text
glow(img, SIZE//2, label_y + 45, tw//2 + 20, 40, WHITE, alpha=30, blur=22)
draw = ImageDraw.Draw(img)
draw.text(((SIZE - tw) // 2, label_y), "AIDE", font=fnt, fill=WHITE)

img.save(OUT)
print(f"Saved {OUT}")
