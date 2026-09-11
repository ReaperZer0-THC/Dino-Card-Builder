
from __future__ import annotations
from typing import Dict, Any, Tuple
from pathlib import Path
import io

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from species_service import render_creature, region_metadata
from vision_adapter_v1 import CARD_STATS, genetic_level

SIZE = (1200, 760)
BG = (7, 16, 10, 255)
PANEL = (15, 27, 18, 242)
PANEL2 = (10, 20, 13, 235)
GOLD = (214, 180, 95, 255)
TEXT = (238, 242, 237, 255)
MUTED = (157, 169, 159, 255)
LINE = (47, 63, 51, 255)
FEMALE = (233, 138, 183, 255)
MALE = (120, 172, 232, 255)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
]
BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
]

def _font(size:int, bold=False):
    candidates = BOLD_CANDIDATES if bold else FONT_CANDIDATES
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def _rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def _fit(img: Image.Image, box: Tuple[int,int,int,int], pad=0) -> Image.Image:
    x1,y1,x2,y2=box
    w=max(1,x2-x1-2*pad); h=max(1,y2-y1-2*pad)
    src=img.copy()
    src.thumbnail((w,h), Image.Resampling.LANCZOS)
    canvas=Image.new("RGBA",(x2-x1,y2-y1),(0,0,0,0))
    canvas.alpha_composite(src, ((canvas.width-src.width)//2,(canvas.height-src.height)//2))
    return canvas

def render_card(record: Dict[str, Any]) -> Image.Image:
    gl = genetic_level(record)
    if gl is None:
        raise ValueError("Record is not card-ready: Genetic Level cannot be calculated.")

    canvas = Image.new("RGBA", SIZE, BG)
    # Subtle vignette
    vignette = Image.new("L", SIZE, 0)
    vd = ImageDraw.Draw(vignette)
    vd.ellipse((-200,-160,1400,950), fill=180)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    glow = Image.new("RGBA", SIZE, (24,45,30,0))
    glow.putalpha(vignette)
    canvas.alpha_composite(glow)

    d = ImageDraw.Draw(canvas)
    _rounded(d,(20,20,1180,740),22,(9,18,12,244),GOLD,2)

    # Header
    d.line((40,118,1160,118), fill=(109,91,46,255), width=2)
    species = record.get("species") or "Unknown Species"
    sex = record.get("sex")
    symbol = "♀" if sex=="female" else "♂"
    symbol_color = FEMALE if sex=="female" else MALE

    d.text((48,42), species, font=_font(42,True), fill=TEXT)
    d.text((48,91), f"Genetic Lvl {gl}", font=_font(20,True), fill=GOLD)
    d.text((1090,40),symbol,font=_font(54,True),fill=symbol_color)

    # Left art panel
    _rounded(d,(42,142,515,646),16,PANEL2,LINE,2)
    creature = render_creature(record)
    art = _fit(creature,(56,158,501,624),8)
    canvas.alpha_composite(art,(56,158))

    # Right stats
    sx,sy=540,148
    d.text((sx,sy),"BREEDING STATS",font=_font(18,True),fill=GOLD)
    headers=["","Wild","Mut","Added"]
    colx=[sx,sx+230,sx+310,sx+395]
    for i,h in enumerate(headers):
        if h:
            d.text((colx[i],sy+34),h,font=_font(13,True),fill=MUTED)
    y=sy+62
    for stat in CARD_STATS:
        row=(record.get("stats") or {}).get(stat,{})
        label=stat.capitalize()
        d.text((sx,y),label,font=_font(17,True),fill=TEXT)
        vals = ["N/A","N/A","N/A"] if row.get("applicable") is False else [
            str(row.get("wild")),str(row.get("mutations")),str(row.get("added"))
        ]
        for i,val in enumerate(vals,1):
            fill = GOLD if (i==2 and val not in ("0","None","N/A")) else TEXT
            d.text((colx[i],y),val,font=_font(17,True),fill=fill)
        d.line((sx,y+27,1145,y+27),fill=LINE,width=1)
        y += 43

    # Color section
    cy=445
    d.text((sx,cy),"COLOR REGIONS",font=_font(18,True),fill=GOLD)
    metas=region_metadata(record)
    card_w=190; card_h=82; gap=10
    for i,m in enumerate(metas):
        row=i//3; col=i%3
        x=sx+col*(card_w+gap); yy=cy+34+row*(card_h+gap)
        active=bool(m.get("visible_on_species"))
        fill=PANEL if active else (17,23,19,220)
        _rounded(d,(x,yy,x+card_w,yy+card_h),10,fill,LINE,1)
        rgb=m.get("rgb") or (128,128,128)
        d.rounded_rectangle((x+10,yy+12,x+46,yy+48),radius=7,fill=tuple(rgb)+(255,),outline=(220,220,220,130),width=1)
        d.text((x+56,yy+9),f"Region {i}",font=_font(13,True),fill=TEXT)
        d.text((x+56,yy+29),f"ID {m.get('color_id')}",font=_font(12,True),fill=GOLD)
        name=m.get("color_name") or "Unknown"
        d.text((x+10,yy+56),name[:24],font=_font(11),fill=TEXT if active else MUTED)
        if not active:
            d.text((x+121,yy+10),"Hidden",font=_font(10,True),fill=MUTED)

    d.text((44,704),"THE HIGH COUNCIL · ARK: SURVIVAL ASCENDED",font=_font(12,True),fill=MUTED)
    return canvas

def render_card_png(record: Dict[str, Any]) -> bytes:
    im = render_card(record)
    buf=io.BytesIO()
    im.save(buf,"PNG")
    return buf.getvalue()
