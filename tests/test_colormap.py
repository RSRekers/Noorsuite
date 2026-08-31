import re

from NoorSuite.colormap import (BUILTIN_NAMES, available_specs, load_file,
                               resolve_spec, sample, save_file)
from NoorSuite.model import ColorMap, SheetModel

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_sample_builtin_continuous_length_and_hex():
    cols = sample("viridis", 5)
    assert len(cols) == 5
    assert all(_HEX.match(c) for c in cols)
    assert len(set(cols)) == 5


def test_sample_builtin_qualitative_cycles():
    cols = sample("tab10", 13)
    assert len(cols) == 13
    assert cols[0] == cols[10]          # wraps at 10


def test_sample_custom_colormap_cycles():
    cm = ColorMap("mine", ["#111111", "#222222", "#333333"])
    assert sample(cm, 2) == ["#111111", "#222222"]
    assert sample(cm, 5) == ["#111111", "#222222", "#333333", "#111111", "#222222"]


def test_sample_zero():
    assert sample("viridis", 0) == []


def test_colormap_roundtrip():
    cm = ColorMap("x", ["#abcdef", "#123456"])
    r = ColorMap.from_dict(cm.to_dict())
    assert r.name == "x" and r.colors == ["#abcdef", "#123456"] and r.builtin is False


def test_available_and_resolve():
    sm = SheetModel("S")
    sm.colormaps = [ColorMap("sheetcm", ["#000000"])]
    lib = [ColorMap("libcm", ["#ffffff"])]
    names = available_specs(sm, lib)
    assert names[:len(BUILTIN_NAMES)] == BUILTIN_NAMES
    assert "libcm" in names and "sheetcm" in names

    assert resolve_spec("viridis", sm, lib) == "viridis"
    assert isinstance(resolve_spec("sheetcm", sm, lib), ColorMap)
    assert resolve_spec("nope", sm, lib) is None


def test_save_load_file_roundtrip(tmp_path):
    cm = ColorMap("exported", ["#010203", "#040506"])
    p = tmp_path / "c.scicmap"
    save_file(str(p), cm)
    back = load_file(str(p))
    assert back.name == "exported"
    assert back.colors == ["#010203", "#040506"]
    assert back.builtin is False
