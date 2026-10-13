"""PowerPoint template -> Beamer theme, and presentations in the document tab."""
import re
import zipfile

import pytest

from NoorSuite import latex, pptximport
from NoorSuite import templates as T
from tests.test_gui_features import app, win, _doc_win  # noqa: F401  (window fixtures)

EMU = 360000


def _png_bytes(w=40, h=20, color=0xFFB02030):
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
    from PyQt6.QtGui import QGuiApplication, QImage
    QGuiApplication.instance() or QGuiApplication([])
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(color)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def _sp(name, x, y, w, h, fill=None, ph=None, text=None, size=None, color=None):
    ph_xml = f'<p:nvPr><p:ph type="{ph[0]}"{" idx=%r" % ph[1] if ph[1] else ""}/></p:nvPr>' \
        if ph else "<p:nvPr/>"
    ph_xml = ph_xml.replace("'", '"')
    fill_xml = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else ""
    rpr = (f'<a:rPr lang="en-US" sz="{int(size * 100)}">'
           + (f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>' if color else "")
           + "</a:rPr>") if size else ""
    body = f"<a:p><a:r>{rpr}<a:t>{text}</a:t></a:r></a:p>" if text else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="{name}"/><p:cNvSpPr/>{ph_xml}</p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{int(x * EMU)}" y="{int(y * EMU)}"/>'
            f'<a:ext cx="{int(w * EMU)}" cy="{int(h * EMU)}"/></a:xfrm>{fill_xml}</p:spPr>'
            f"<p:txBody><a:bodyPr/>{body}</p:txBody></p:sp>")


def _pic(name, rid, x, y, w, h):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="3" name="{name}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/></p:blipFill><p:spPr><a:xfrm>'
            f'<a:off x="{int(x * EMU)}" y="{int(y * EMU)}"/><a:ext cx="{int(w * EMU)}" '
            f'cy="{int(h * EMU)}"/></a:xfrm></p:spPr></p:pic>')


def _make_pptx(path):
    ns = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
    theme = ('<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements>'
             '<a:clrScheme name="x"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr '
             'val="FFFFFF"/></a:lt1><a:accent1><a:srgbClr val="336699"/></a:accent1></a:clrScheme>'
             '<a:fontScheme><a:majorFont><a:latin typeface="Open Sans Light"/></a:majorFont>'
             '<a:minorFont><a:latin typeface="Open Sans Light"/></a:minorFont></a:fontScheme>'
             "</a:themeElements></a:theme>")
    master = (f"<p:sldMaster {ns}><p:cSld><p:spTree>"
              + _sp("Bar", 0, 0, 33.867, 2.5, fill="BE1E3C")
              + _sp("Title", 0.7, 0.4, 30.7, 1.6, ph=("title", ""))
              + _sp("Body", 0.7, 3.2, 32.5, 15.3, ph=("body", "1"))
              + _pic("Logo", "rId2", 0.2, 18.9, 5.5, 2.1)
              + _sp("Num", 31.8, 0.6, 1.4, 1.0, ph=("sldNum", ""), text="1", size=20, color="FFFFFF")
              + "</p:spTree></p:cSld><p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz=\"3600\">"
              "<a:solidFill><a:srgbClr val=\"FFFFFF\"/></a:solidFill></a:defRPr></a:lvl1pPr>"
              "</p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz=\"2400\"/></a:lvl1pPr>"
              "</p:bodyStyle></p:txStyles></p:sldMaster>")
    layout = (f'<p:sldLayout {ns} showMasterSp="0"><p:cSld name="Title"><p:spTree>'
              + _pic("Photo", "rId2", 0, 0, 33.867, 14.4)
              + _sp("Title", 0.7, 14.7, 26.9, 3.2, ph=("body", "13"), text="x", size=38,
                    color="0069B3")
              + _sp("Name", 0.7, 17.9, 26.9, 1.0, ph=("body", "14"), text="x", size=20)
              + _sp("Date", 0.7, 18.9, 26.9, 1.8, ph=("body", "15"), text="x", size=18)
              + _sp("Credit", 27.8, 13.5, 6.1, 0.9, fill="FFFFFF", text="Photo: Someone", size=14)
              + "</p:spTree></p:cSld></p:sldLayout>")
    content = f"<p:sldLayout {ns}><p:cSld name=\"Content\"><p:spTree/></p:cSld></p:sldLayout>"
    slide1 = f"<p:sld {ns}><p:cSld><p:spTree/></p:cSld></p:sld>"
    rel = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
           '{}</Relationships>')
    png = _png_bytes()
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml",
                   f'<p:presentation {ns}><p:sldSz cx="{int(33.867 * EMU)}" cy="{int(21.167 * EMU)}"/>'
                   "</p:presentation>")
        z.writestr("ppt/theme/theme1.xml", theme)
        z.writestr("ppt/slideMasters/slideMaster1.xml", master)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", rel.format(
            '<Relationship Id="rId2" Target="../media/logo.png"/>'))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", rel.format(
            '<Relationship Id="rId2" Target="../media/photo.png"/>'))
        z.writestr("ppt/slideLayouts/slideLayout2.xml", content)
        z.writestr("ppt/slides/slide1.xml", slide1)
        z.writestr("ppt/slides/_rels/slide1.xml.rels", rel.format(
            '<Relationship Id="rId1" Target="../slideLayouts/slideLayout1.xml"/>'))
        z.writestr("ppt/media/logo.png", png)
        z.writestr("ppt/media/photo.png", _png_bytes(80, 30, 0xFF2060A0))


@pytest.fixture
def pres_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOORSUITE_PRESENTATIONS", str(tmp_path / "pres"))
    monkeypatch.setenv("NOORSUITE_TEMPLATES", str(tmp_path / "tpl"))
    p = tmp_path / "Corporate Template.pptx"
    _make_pptx(p)
    return p


def test_pptx_geometry_is_read(pres_env):
    P = pptximport.parse(pres_env)
    assert abs(P["size_cm"][0] - 33.867) < 0.01
    bars = [i for i in P["master"]["items"] if i.get("fill") == "BE1E3C"]
    assert bars and abs(bars[0]["h"] - 2.5) < 1e-6
    assert P["title_style"] == {"size": 36.0, "color": "FFFFFF"}
    assert P["fonts"][0] == "Open Sans Light"
    lay = P["layouts"]["ppt/slideLayouts/slideLayout1.xml"]
    assert lay["hides_master"] and lay["name"] == "Title"


def test_import_writes_theme_media_and_a_beamer_layout(pres_env, tmp_path):
    res = pptximport.import_pptx(pres_env)
    assert res["theme"] == "corporatetemplate" and res["id"] == "user:corporatetemplate"
    d = tmp_path / "pres" / "corporatetemplate"
    sty = (d / "beamerthemecorporatetemplate.sty").read_text(encoding="utf8")
    assert r"\fill[corporatetemplatec0]" in sty and "BE1E3C" in sty        # the red bar
    assert "Photo: Someone" in sty and r"\inserttitle" in sty and r"\insertdate" in sty
    assert (d / "corporatetemplate-logo.jpg").is_file() or \
        (d / "corporatetemplate-logo.png").is_file()
    # the layout is listed as a presentation layout of the beamer base
    assert res["id"] in dict(T.list_templates()) and T.base_of(res["id"]) == "beamer"
    doc = T.build_document(res["id"], "My talk", ["A. One"], "", "")
    assert r"\documentclass[aspectratio=1610,11pt,t]{beamer}" in doc
    assert r"\usetheme{corporatetemplate}" in doc and r"\titlepage" in doc
    assert T.detect(doc) == "beamer"


def test_theme_files_travel_with_the_document(pres_env, tmp_path):
    res = pptximport.import_pptx(pres_env)
    doc = T.build_document(res["id"], "T", ["A"], "", "")
    files = pptximport.support_files(doc)
    assert "beamerthemecorporatetemplate.sty" in files and len(files) >= 3
    wd = latex.prepare_workdir(tmp_path / "wd", doc)
    assert (wd / "beamerthemecorporatetemplate.sty").is_file()
    assert pptximport.support_files(r"\usetheme{Madrid}") == {}


@pytest.mark.skipif(latex.find_engine() is None, reason="no LaTeX engine installed")
def test_generated_theme_compiles(pres_env, tmp_path):
    res = pptximport.import_pptx(pres_env)
    body = (r"\begin{frame}{A frame}" "\n" r"\begin{itemize}\item One\item Two\end{itemize}" "\n"
            r"\framesource{Source: test}" "\n" r"\end{frame}" "\n")
    doc = T.build_document(res["id"], "My talk", ["A. One"], "", body)
    latex.prepare_workdir(tmp_path / "w", doc)
    out = latex.build_pdf(tmp_path / "w")
    assert out.ok, out.log[-800:]


def test_beamer_switching_keeps_frames_and_does_not_double_the_title(pres_env):
    res = pptximport.import_pptx(pres_env)
    plain = T.build_document("beamer", "T", ["A"], "", r"\begin{frame}{X}Hi\end{frame}")
    mine = T.apply_template(plain, res["id"])
    assert mine.count(r"\titlepage") == 1 and r"\begin{frame}{X}" in mine
    assert r"\usetheme{corporatetemplate}" in mine
    back = T.apply_template(mine, "beamer")
    assert back.count(r"\titlepage") == 1 and r"\usetheme{default}" in back
    # citations in a presentation do not get natbib injected
    assert not latex.own_citations(plain)


def test_figure_frames_and_layout_choices_in_the_document_tab(win, pres_env):
    res = pptximport.import_pptx(pres_env)
    ids, dm, tab = _doc_win(win, 1)
    tab.set_source(T.build_document(res["id"], "Talk", ["A"], "", r"\begin{frame}{X}Hi\end{frame}"))
    tab.model.layout = res["id"]
    assert tab.current_layout() == res["id"]
    labels = [tab.layout_combo.itemData(i) for i in range(tab.layout_combo.count())]
    assert "beamer" in labels and res["id"] in labels and "acs" not in labels
    from PyQt6.QtCore import Qt
    fp = tab.figures
    for k in range(fp.list.count()):
        if fp.list.item(k).data(Qt.ItemDataRole.UserRole) == ids[0]:
            fp.list.item(k).setSelected(True)
    snip = fp.snippets_for(fp.list.selectedItems())
    assert snip.startswith(r"\begin{frame}{") and r"\noorsheet[width=0.85\textwidth]{" in snip
    assert r"\begin{figure}" not in snip
    # an article gets article layouts, not presentation ones
    tab.set_source(T.build_document("article", "T", ["A"], "A.", "B."))
    tab.sync_layout_combo()
    ids2 = [tab.layout_combo.itemData(i) for i in range(tab.layout_combo.count())]
    assert "acs" in ids2 and "beamer" not in ids2


def test_new_presentation_from_the_window(win, pres_env):
    res = pptximport.import_pptx(pres_env)
    dm = win.create_new_document("Group talk", template=res["id"])
    assert r"\usetheme{corporatetemplate}" in dm.source and "Group talk" in dm.source
    plain = win.create_new_document("Plain talk", template="beamer")
    assert r"\documentclass[aspectratio=1610,11pt,t]{beamer}" in plain.source
