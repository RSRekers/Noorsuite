"""Turn a PowerPoint template (.pptx) into a LaTeX Beamer theme.

PowerPoint stores a corporate design as a *slide master* (bars, logos, the title and body
placeholders) plus *layouts* (title slides with photos ...). This module reads exactly that
geometry -- positions in EMU, fill colours, pictures with their crop, static text boxes,
placeholder font sizes and colours -- and writes

* ``beamertheme<name>.sty``: the master drawn as a TikZ background, title-bar title, slide number,
  footer, bullet style and a title page built from the title layout,
* the pictures (cropped, converted: SVG / EMF -> PDF through Inkscape, TIFF -> JPEG),
* a user layout (``~/.noorsuite/templates/<id>.json``, base ``beamer``) so *New presentation* lists it.

Everything lands in ``~/.noorsuite/presentations/<name>/``; the theme files are copied next to the
document at build time (see :func:`support_files`), so the document stays self-contained.
Lengths are scaled from the slide size to Beamer's 16 x 10 cm page.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from pathlib import Path

EMU_CM = 360000.0
BEAMER_W_CM = 16.0
_SCHEME = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


def presentations_dir() -> Path:
    d = os.environ.get("NOORSUITE_PRESENTATIONS")
    return Path(d) if d else Path(os.path.expanduser("~")) / ".noorsuite" / "presentations"


# ------------------------------------------------------------------------- parsing
def _theme_colors(xml: str) -> dict:
    out = {}
    for name in ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4",
                 "accent5", "accent6", "hlink", "folHlink"):
        m = re.search(rf"<a:{name}>.*?(?:val|lastClr)=\"([0-9A-Fa-f]{{6}})\"", xml, re.S)
        if m:
            out[name] = m.group(1).upper()
    return out


def _color(frag: str, theme: dict):
    m = re.search(r'<a:srgbClr val="([0-9A-Fa-f]{6})"', frag)
    if m:
        return m.group(1).upper()
    m = re.search(r'<a:schemeClr val="(\w+)"', frag)
    if m:
        key = _SCHEME.get(m.group(1), m.group(1))
        return theme.get(key)
    return None


def _rels(zf, part: str) -> dict:
    d, f = part.rsplit("/", 1)
    try:
        t = zf.read(f"{d}/_rels/{f}.rels").decode("utf8")
    except KeyError:
        return {}
    out = {}
    for m in re.finditer(r"<Relationship ([^>]*)/>", t):
        a = m.group(1)
        i, g = re.search(r'Id="([^"]*)"', a), re.search(r'Target="([^"]*)"', a)
        if i and g:
            tgt = g.group(1)
            if tgt.startswith("../"):
                tgt = "ppt/" + tgt[3:]
            elif not tgt.startswith("ppt/"):
                tgt = f"{d}/{tgt}"
            out[i.group(1)] = tgt
    return out


def _shapes(xml: str, theme: dict, rels: dict) -> list:
    items = []
    for m in re.finditer(r"<p:(sp|pic)>(.*?)</p:\1>", xml, re.S):
        kind, b = m.group(1), m.group(2)
        off = re.search(r'<a:off x="(-?\d+)" y="(-?\d+)"', b)
        ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', b)
        it = {"kind": kind, "name": (re.search(r'name="([^"]*)"', b) or [None, ""])[1]}
        if off and ext:
            it.update(x=int(off.group(1)) / EMU_CM, y=int(off.group(2)) / EMU_CM,
                      w=int(ext.group(1)) / EMU_CM, h=int(ext.group(2)) / EMU_CM)
        ph = re.search(r"<p:ph([^>]*)>", b)
        if ph:
            a = ph.group(1)
            it["ph"] = (re.search(r'type="(\w+)"', a) or [None, "body"])[1]
            it["idx"] = (re.search(r'idx="(\d+)"', a) or [None, ""])[1]
        sp = re.search(r"<p:spPr>(.*?)</p:spPr>", b, re.S)
        if sp and "<a:solidFill>" in sp.group(1):
            fill = re.search(r"<a:solidFill>(.*?)</a:solidFill>", sp.group(1), re.S)
            it["fill"] = _color(fill.group(1), theme) if fill else None
        if kind == "pic":
            e = re.search(r'r:embed="(rId\d+)"', b)
            it["media"] = rels.get(e.group(1)) if e else None
            c = re.search(r'<a:srcRect([^>]*)/>', b)
            if c:
                it["crop"] = {k: int(v) / 100000.0 for k, v in
                              re.findall(r'(\w)="(-?\d+)"', c.group(1))}
        txt = re.findall(r"<a:t>([^<]*)</a:t>", b)
        if txt and "ph" not in it:
            it["text"] = "".join(txt)
        rpr = re.search(r"<a:rPr[^>]*sz=\"(\d+)\"[^>]*>(.*?)</a:rPr>", b, re.S)
        if rpr:
            it["size"] = int(rpr.group(1)) / 100.0
            it["color"] = _color(rpr.group(2), theme)
        elif re.search(r'sz="(\d+)"', b):
            it["size"] = int(re.search(r'sz="(\d+)"', b).group(1)) / 100.0
        if "color" not in it:
            fc = re.search(r"<a:(?:defRPr|rPr|endParaRPr)[^>]*>.*?<a:solidFill>(.*?)</a:solidFill>",
                           b, re.S)
            it["color"] = _color(fc.group(1), theme) if fc else None
        items.append(it)
    return items


def parse(path) -> dict:
    """The geometry of a .pptx: ``{size_cm, theme, fonts, master, layouts, title_slide,
    content_slide, title_style, body_style}`` (all lengths in cm of the *PowerPoint* slide)."""
    zf = zipfile.ZipFile(path)
    pres = zf.read("ppt/presentation.xml").decode("utf8")
    sz = re.search(r'<p:sldSz cx="(\d+)" cy="(\d+)"', pres)
    size = (int(sz.group(1)) / EMU_CM, int(sz.group(2)) / EMU_CM)
    th = zf.read("ppt/theme/theme1.xml").decode("utf8")
    theme = _theme_colors(th)
    fonts = re.findall(r'<a:(?:major|minor)Font>\s*<a:latin typeface="([^"]*)"', th)
    mpart = "ppt/slideMasters/slideMaster1.xml"
    mxml = zf.read(mpart).decode("utf8")
    master = {"items": _shapes(mxml, theme, _rels(zf, mpart)), "xml": mxml}
    ts = re.search(r"<p:titleStyle>.*?<a:defRPr[^>]*sz=\"(\d+)\"[^>]*>(.*?)</a:defRPr>", mxml, re.S)
    title_style = {"size": int(ts.group(1)) / 100.0, "color": _color(ts.group(2), theme)} if ts \
        else {"size": 36.0, "color": "FFFFFF"}
    bs = re.search(r"<p:bodyStyle>.*?<a:lvl1pPr[^>]*>.*?<a:defRPr[^>]*sz=\"(\d+)\"", mxml, re.S)
    body_style = {"size": int(bs.group(1)) / 100.0 if bs else 24.0}
    layouts = {}
    for n in zf.namelist():
        if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n):
            x = zf.read(n).decode("utf8")
            layouts[n] = {"name": (re.search(r'<p:cSld name="([^"]*)"', x) or [None, n])[1],
                          "items": _shapes(x, theme, _rels(zf, n)),
                          "hides_master": 'showMasterSp="0"' in x}
    slides = []
    for n in sorted((n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda s: int(re.search(r"(\d+)", s.rsplit("/", 1)[1]).group(1))):
        r = _rels(zf, n)
        lay = next((v for v in r.values() if "slideLayout" in v), None)
        x = zf.read(n).decode("utf8")
        slides.append({"layout": lay, "items": _shapes(x, theme, r)})
    title_slide = slides[0] if slides else None
    content_slide = next((s for s in slides[1:] if s["layout"] != (title_slide or {}).get("layout")),
                         None)
    return {"size_cm": size, "theme": theme, "fonts": fonts, "master": master,
            "layouts": layouts, "slides": slides, "title_slide": title_slide,
            "content_slide": content_slide, "title_style": title_style,
            "body_style": body_style, "zip": zf}


# ------------------------------------------------------------------------- media
def _convert_media(zf, member: str, out_dir: Path, stem: str, crop=None, max_px=2400):
    """Write ``member`` of the pptx as a LaTeX-includable file; returns its file name or None."""
    from . import assets as assets_mod
    ext = member.rsplit(".", 1)[-1].lower()
    data = zf.read(member)
    out_dir.mkdir(parents=True, exist_ok=True)
    if ext in ("svg", "emf", "wmf"):
        src = out_dir / f"_{stem}.{ext}"
        src.write_bytes(data)
        pdf = out_dir / f"{stem}.pdf"
        ok = assets_mod._run_inkscape([str(src), "--export-type=pdf",
                                       f"--export-filename={pdf}", "--export-area-page"])
        src.unlink(missing_ok=True)
        return pdf.name if ok and pdf.is_file() else None
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage
    except ImportError:
        (out_dir / f"{stem}.{ext}").write_bytes(data)
        return f"{stem}.{ext}"
    img = QImage.fromData(data)
    if img.isNull():
        return None
    if crop:
        w, h = img.width(), img.height()
        l, t = int(w * crop.get("l", 0)), int(h * crop.get("t", 0))
        r, b = int(w * crop.get("r", 0)), int(h * crop.get("b", 0))
        img = img.copy(l, t, max(1, w - l - r), max(1, h - t - b))
    if max(img.width(), img.height()) > max_px:
        img = img.scaled(max_px, max_px, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    if img.hasAlphaChannel() and ext == "png":
        name = f"{stem}.png"
        img.save(str(out_dir / name), "PNG")
    else:
        name = f"{stem}.jpg"
        img.convertToFormat(QImage.Format.Format_RGB32).save(str(out_dir / name), "JPG", 92)
    return name


# ------------------------------------------------------------------------- generation
def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "", str(text)).lower()
    return s or "pptxtheme"


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def import_pptx(path, name: str | None = None, out_root=None) -> dict:
    """Convert ``path`` into a Beamer theme + user layout; returns ``{"id", "label", "theme",
    "dir", "notes"}``. Raises ``ValueError`` for files without a slide master."""
    P = parse(path)
    zf = P["zip"]
    label = name or Path(path).stem
    theme_name = _slug(label)
    root = Path(out_root) if out_root else presentations_dir()
    folder = root / theme_name
    folder.mkdir(parents=True, exist_ok=True)
    k = BEAMER_W_CM / P["size_cm"][0]               # PowerPoint cm -> Beamer cm
    pt = k                                           # font sizes scale the same way
    notes = []
    theme = P["theme"]

    media: dict = {}                                # zip member + crop -> file name

    def pic(member, crop=None, tag=""):
        key = (member, json.dumps(crop, sort_keys=True))
        if key not in media:
            stem = f"{theme_name}-{Path(member).stem}{tag}"
            fn = _convert_media(zf, member, folder, stem, crop)
            media[key] = fn
            if fn is None:
                notes.append(f"could not convert {member}")
        return media[key]

    def at(x, y):
        return f"($(current page.north west)+({_fmt(x * k)}cm,{_fmt(-y * k)}cm)$)"

    def draw_items(items, skip_ph=True) -> str:
        out = []
        for it in items:
            if "x" not in it or ("ph" in it and skip_ph):
                continue
            x, y, w, h = it["x"], it["y"], it["w"], it["h"]
            if it["kind"] == "pic" and it.get("media"):
                fn = pic(it["media"], it.get("crop"))
                if fn:
                    out.append(f"  \\node[anchor=north west,inner sep=0pt] at {at(x, y)} "
                               f"{{\\includegraphics[width={_fmt(w * k)}cm,height={_fmt(h * k)}cm]"
                               f"{{{fn}}}}};")
            elif it["kind"] == "sp" and it.get("fill") and "text" not in it:
                out.append(f"  \\fill[{_colname(it['fill'])}] {at(x, y)} rectangle "
                           f"{at(x + w, y + h)};")
            elif it.get("text"):
                size = (it.get("size") or 14.0) * pt
                col = it.get("color") or "000000"
                bg = f"fill={_colname(it['fill'])}," if it.get("fill") else ""
                out.append(
                    f"  \\node[anchor=north west,inner sep=1pt,{bg}text width={_fmt(w * k)}cm,"
                    f"font=\\fontsize{{{_fmt(size)}}}{{{_fmt(size * 1.2)}}}\\selectfont,"
                    f"text={_colname(col)}] at {at(x, y)} {{{_tex(it['text'])}}};")
        return "\n".join(out)

    colors: dict = {}

    def _colname(hexv):
        hexv = (hexv or "000000").upper()
        colors.setdefault(hexv, f"{theme_name}c{len(colors)}")
        return colors[hexv]

    def _tex(t):
        return re.sub(r"([#$%&_{}])", r"\\\1", t)

    master_items = P["master"]["items"]
    # --- title-bar geometry from the master placeholders --------------------------------
    title_ph = next((i for i in master_items if i.get("ph") == "title" and "x" in i), None)
    num_ph = next((i for i in master_items if i.get("ph") == "sldNum" and "x" in i), None)
    body_ph = next((i for i in master_items if i.get("ph") == "body" and "x" in i), None)
    bars = [i for i in master_items if i["kind"] == "sp" and i.get("fill") and "ph" not in i
            and "text" not in i and "x" in i]
    bar_h = max([b["y"] + b["h"] for b in bars if b["y"] < 1.0] or [2.5])
    body_top = (body_ph["y"] if body_ph else bar_h + 0.7)
    content_layout = (P["content_slide"] or {}).get("layout")
    footer_ph = None
    if content_layout and content_layout in P["layouts"]:
        footer_ph = next((i for i in P["layouts"][content_layout]["items"]
                          if i.get("ph") == "body" and "x" in i and i["y"] > 15), None)
    # --- title layout: the layout of slide 1 --------------------------------------------
    tl = (P["title_slide"] or {}).get("layout")
    tl_items = P["layouts"].get(tl, {}).get("items", [])
    credit = next((i for i in tl_items if i.get("text") and i["y"] < 15 and i["y"] > 10), None)
    texts = sorted([i for i in tl_items if i.get("ph") == "body" and "x" in i],
                   key=lambda i: i["y"])
    # the three text boxes of a title layout: title (largest), then author, then date
    t_by_size = sorted(texts, key=lambda i: -(i.get("size") or 0))
    title_box = t_by_size[0] if t_by_size else None
    rest = sorted([t for t in texts if t is not title_box], key=lambda i: i["y"])
    name_box = rest[0] if rest else None
    date_box = rest[1] if len(rest) > 1 else None
    num_color = (num_ph or {}).get("color") or "FFFFFF"

    base = (num_ph or {}).get("size") or 20.0
    master_draw = draw_items([i for i in master_items if i is not title_ph and i is not num_ph
                              and i is not body_ph])
    if tl and tl in P["layouts"] and P["layouts"][tl]["items"]:
        title_draw = draw_items([i for i in tl_items if i not in texts and i is not credit
                                and i.get("ph") != "sldNum"])
    else:
        title_draw = ""
    # the other title layouts' pictures are offered through \titleimage
    alt = []
    for ln, lay in sorted(P["layouts"].items()):
        if ln == tl:
            continue
        ph_n = sum(1 for i in lay["items"] if i.get("ph") == "body")
        for it in lay["items"]:
            if it["kind"] == "pic" and it.get("media") and it.get("w", 0) > 20 \
                    and it.get("h", 0) > 8 and ph_n >= 3:
                fn = pic(it["media"], it.get("crop"))
                if fn:
                    alt.append(fn)
    bg_main = next((i for i in tl_items if i["kind"] == "pic" and i.get("w", 0) > 20
                    and i.get("media")), None)
    bg_main_fn = pic(bg_main["media"], bg_main.get("crop")) if bg_main else None

    def box_node(it, content, default_color):
        if not it:
            return ""
        size = (it.get("size") or 18.0) * pt
        col = it.get("color") or default_color
        anchor = "south west" if it is title_box else "north west"
        yy = it["y"] + it["h"] if it is title_box else it["y"]
        return (f"  \\node[anchor={anchor},inner sep=0pt,text width={_fmt(it['w'] * k)}cm,"
                f"font=\\fontsize{{{_fmt(size)}}}{{{_fmt(size * 1.15)}}}\\selectfont,"
                f"text={_colname(col)}] at {at(it['x'], yy)} {{{content}}};")

    title_nodes = "\n".join(filter(None, [
        box_node(title_box, r"\inserttitle", "0069B3"),
        box_node(name_box, r"\insertauthor", "000000"),
        box_node(date_box, r"\insertdate", "000000")]))
    _colname("000000")
    _colname("FFFFFF")
    bar_col = bars[0]["fill"] if bars else theme.get("accent1", "BE1E3C")
    _colname(bar_col)
    title_col = P["title_style"].get("color") or "FFFFFF"
    _colname(title_col)
    _colname(num_color)

    f_title = P["title_style"]["size"] * pt
    f_body = P["body_style"]["size"] * pt
    f_num = base * pt
    f_src = ((footer_ph or {}).get("size") or 14.0) * pt
    font_name = (P["fonts"] or ["Open Sans Light"])[0]
    color_defs = "\n".join(f"\\definecolor{{{n}}}{{HTML}}{{{h}}}" for h, n in colors.items())

    # geometry in Beamer cm, computed here so the template below stays readable
    tp = title_ph or {"x": 0.7, "y": 0.4, "w": 30.7, "h": 1.6}
    npd = num_ph or {"x": 31.8, "y": 0.6, "w": 1.4, "h": 1.0}
    bp = body_ph or {"x": 0.7, "y": 3.2, "w": 32.5, "h": 15.3}
    fp = footer_ph or {"x": 6.0, "y": 18.9, "w": 20.7, "h": 1.9}
    page_w, page_h = P["size_cm"]
    margin_l = bp["x"] * k
    margin_r = max(0.0, (page_w - (bp["x"] + bp["w"])) * k)
    foot_h = max(0.1, (page_h - (bp["y"] + bp["h"])) * k)
    title_w = tp["w"] * k
    title_pos = at(tp["x"], tp["y"] + tp["h"] / 2)
    num_pos = at(npd["x"] + npd["w"] / 2, npd["y"] + npd["h"] / 2)
    src_pos = at(fp["x"], fp["y"])
    src_w = fp["w"] * k
    cr = credit or {"x": 27.8, "y": 13.5, "w": 6.1, "h": 0.9}
    credit_pos = at(cr["x"] + cr["w"], cr["y"] + cr["h"])
    credit_text = _tex(credit["text"]) if credit else ""
    tn = theme_name
    title_page_draw = title_draw
    if bg_main_fn:
        title_page_draw = title_draw.replace(bg_main_fn, "\\csname " + tn + "@titleimage\\endcsname")
    c_black, c_white = _colname("000000"), _colname("FFFFFF")
    color_defs = "\n".join(f"\\definecolor{{{n}}}{{HTML}}{{{h}}}" for h, n in colors.items())
    ff = font_name

    sty = (
        "% Generated by NoorSuite from a PowerPoint template (" + Path(path).name + ").\n"
        "\\NeedsTeXFormat{LaTeX2e}\n"
        "\\ProvidesPackage{beamertheme" + tn + "}[2026/10/13 PowerPoint template as Beamer theme]\n"
        "\\RequirePackage{tikz}\n\\usetikzlibrary{calc}\n\\RequirePackage{etoolbox}\n"
        "\\RequirePackage{iftex}\n"
        "\\ifXeTeX\n  \\RequirePackage{fontspec}\n"
        "  % " + ff + ": the bundled TeX Live files (OpenSans-*.ttf) when the family is not installed\n"
        "  \\IfFontExistsTF{" + ff + "}{\\setsansfont{" + ff + "}}{%\n"
        "    \\IfFontExistsTF{OpenSans-Light.ttf}{%\n"
        "      \\setsansfont{OpenSans-Light.ttf}[BoldFont=OpenSans-SemiBold.ttf,\n"
        "        ItalicFont=OpenSans-LightItalic.ttf,BoldItalicFont=OpenSans-SemiBoldItalic.ttf]}{}}\n"
        "\\fi\n"
        "\\renewcommand{\\familydefault}{\\sfdefault}\n"
        + color_defs + "\n"
        "\\setbeamertemplate{navigation symbols}{}\n"
        "\\setbeamercolor{normal text}{fg=black,bg=white}\n"
        "\\setbeamercolor{frametitle}{fg=" + _colname(title_col) + "}\n"
        "\\setbeamerfont{frametitle}{size=\\fontsize{" + _fmt(f_title) + "}{" + _fmt(f_title * 1.1)
        + "}\\selectfont}\n"
        "\\setbeamerfont{itemize/enumerate body}{size=\\fontsize{" + _fmt(f_body) + "}{"
        + _fmt(f_body * 1.2) + "}\\selectfont}\n"
        "\\setbeamerfont{itemize/enumerate subbody}{size=\\fontsize{" + _fmt(f_body * 0.92) + "}{"
        + _fmt(f_body * 1.1) + "}\\selectfont}\n"
        "\\setbeamertemplate{itemize item}{\\raisebox{0.15ex}{\\scriptsize$\\circ$}}\n"
        "\\setbeamertemplate{itemize subitem}{\\raisebox{0.15ex}{\\scriptsize$\\circ$}}\n"
        "\\setbeamercolor{itemize item}{fg=black}\n\\setbeamercolor{itemize subitem}{fg=black}\n"
        "\\setbeamersize{text margin left=" + _fmt(margin_l) + "cm,text margin right="
        + _fmt(margin_r) + "cm}\n\n"
        "% slide background: master bars, logos and static texts\n"
        "\\setbeamertemplate{background}{%\n"
        "  \\begin{tikzpicture}[remember picture,overlay]\n" + master_draw + "\n"
        "  \\end{tikzpicture}}\n\n"
        "% frame title in the title bar; the body starts at " + _fmt(body_top * k) + "cm\n"
        "\\setbeamertemplate{frametitle}{%\n  \\nointerlineskip\n"
        "  \\vbox to " + _fmt(body_top * k) + "cm{%\n"
        "    \\begin{tikzpicture}[remember picture,overlay]\n"
        "      \\node[anchor=west,inner sep=0pt,text width=" + _fmt(title_w) + "cm] at " + title_pos
        + "\n        {\\usebeamerfont{frametitle}\\usebeamercolor[fg]{frametitle}\\insertframetitle};\n"
        "    \\end{tikzpicture}%\n    \\vss}}\n\n"
        "% footer: slide number (top right, as in the template) and a free source line\n"
        "\\newcommand{\\framesource}[1]{\\gdef\\" + tn + "@source{#1}}\n"
        "\\gdef\\" + tn + "@source{}\n"
        "\\newcommand{\\imagecredit}[1]{\\gdef\\" + tn + "@credit{#1}}\n"
        "\\newcommand{\\titleimage}[1]{\\gdef\\" + tn + "@titleimage{#1}}\n"
        "\\gdef\\" + tn + "@credit{" + credit_text + "}\n"
        "\\gdef\\" + tn + "@titleimage{" + (bg_main_fn or "") + "}\n"
        "\\AtBeginEnvironment{frame}{\\gdef\\" + tn + "@source{}}\n"
        "\\setbeamertemplate{footline}{%\n  \\nointerlineskip\n"
        "  \\vbox to " + _fmt(foot_h) + "cm{\\vss}%\n"
        "  \\begin{tikzpicture}[remember picture,overlay]\n"
        "    \\node[anchor=center,font=\\fontsize{" + _fmt(f_num) + "}{" + _fmt(f_num * 1.2)
        + "}\\selectfont,text=" + _colname(num_color) + "] at " + num_pos
        + " {\\insertframenumber};\n"
        "    \\node[anchor=north west,inner sep=0pt,text width=" + _fmt(src_w) + "cm,align=center,\n"
        "      font=\\fontsize{" + _fmt(f_src) + "}{" + _fmt(f_src * 1.15) + "}\\selectfont]\n"
        "      at " + src_pos + " {\\csname " + tn + "@source\\endcsname};\n"
        "  \\end{tikzpicture}}\n\n"
        "% title page: the title layout (picture, logo block, title / author / date)\n"
        "\\setbeamertemplate{title page}{%\n"
        "  \\begin{tikzpicture}[remember picture,overlay]\n"
        "    \\fill[white] (current page.north west) rectangle (current page.south east);\n"
        + title_page_draw + "\n" + title_nodes + "\n"
        "    \\node[anchor=south east,inner sep=1pt,fill=white,font=\\fontsize{" + _fmt(14 * pt)
        + "}{" + _fmt(16 * pt) + "}\\selectfont,text=" + c_black + "]\n"
        "      at " + credit_pos + " {\\csname " + tn + "@credit\\endcsname};\n"
        "  \\end{tikzpicture}}\n"
        "\\endinput\n")
    (folder / f"beamertheme{theme_name}.sty").write_text(sty, encoding="utf-8")
    meta = {"label": label, "theme": theme_name, "source": Path(path).name,
            "alt_title_images": alt, "notes": notes}
    (folder / "template.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # user layout (base: beamer) so New presentation lists it
    from . import templates
    pre = (r"\documentclass[aspectratio=1610,11pt,t]{beamer}" "\n"
           r"\usepackage{graphicx}" "\n" rf"\usetheme{{{theme_name}}}" "\n"
           r"\usepackage{noorsuite}   % \noorsheet{...}: NoorSuite sheets as figures" "\n@EXTRA@")
    b = templates.TEMPLATES["beamer"]
    tpl = {"label": label, "base": "beamer", "class": "beamer", "two_column": False,
           "preamble": pre, "front": b["front"], "tail": b["tail"],
           "widths": [list(w) for w in b["widths"]], "word": dict(b["word"]),
           "ack": "", "known": ["graphicx", "noorsuite", theme_name], "theme": theme_name}
    tdir = templates.user_dir()
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{theme_name}.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
    return {"id": f"user:{theme_name}", "label": label, "theme": theme_name,
            "dir": str(folder), "notes": notes}


# --------------------------------------------------------------- build-time support files
def support_files(source: str) -> dict:
    r"""``{relative name: bytes}`` of every imported theme a document uses (``\usetheme{x}``):
    the ``beamertheme<x>.sty`` and its pictures, to be written next to ``main.tex``."""
    out = {}
    root = presentations_dir()
    if not root.is_dir():
        return out
    code = re.sub(r"(?<!\\)%.*", "", source)
    for m in re.finditer(r"\\usetheme(?:\[[^\]]*\])?\{([^}]*)\}", code):
        for name in (n.strip() for n in m.group(1).split(",")):
            d = root / name
            if (d / f"beamertheme{name}.sty").is_file():
                for f in d.iterdir():
                    if f.is_file() and f.name != "template.json":
                        out[f.name] = f.read_bytes()
    return out


def main(argv) -> int:
    """``python -m NoorSuite presentation import FILE.pptx [--name NAME]`` /
    ``presentation list``."""
    import argparse
    ap = argparse.ArgumentParser(prog="noorsuite presentation")
    ap.add_argument("command", choices=["import", "list"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--name")
    a = ap.parse_args(argv)
    if a.command == "list":
        root = presentations_dir()
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if (d / "template.json").is_file():
                meta = json.loads((d / "template.json").read_text(encoding="utf-8"))
                print(f"{d.name}\t{meta.get('label', '')}")
        return 0
    if not a.file:
        ap.error("import needs a .pptx file")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])           # noqa: F841 (keep alive)
    res = import_pptx(a.file, a.name)
    print(f"imported {res['label']!r}: theme {res['theme']} -> {res['dir']}")
    for n in res["notes"]:
        print("note:", n)
    return 0
