from NoorSuite.fuzzy import fuzzy_match, fuzzy_score


def test_empty_query_matches_everything():
    assert fuzzy_match("", "anything")
    assert fuzzy_match("   ", "")


def test_subsequence_hits():
    assert fuzzy_match("smpllbl", "Sample label")
    assert fuzzy_match("PL", "Photoluminescence batch")
    assert fuzzy_match("ivsw", "IV_sweep.current")


def test_subsequence_misses():
    assert not fuzzy_match("zzz", "Sample label")
    assert not fuzzy_match("lbas", "Sample label")   # wrong order


def test_case_insensitive():
    assert fuzzy_match("SAMPLE", "a sample trace")


def test_score_prefers_contiguous_and_early():
    assert fuzzy_score("sample", "sample trace") > fuzzy_score("sample", "the sample")
    assert fuzzy_score("smpl", "sample") > 0.0
    assert fuzzy_score("zzz", "sample") == 0.0
    assert fuzzy_score("", "sample") == 1.0


def test_smart_score_is_specific():
    from NoorSuite.fuzzy import parse_query, smart_score
    big = [("name", 1.0, "Response map soc_std_end"), ("tags", 0.9, "thermal hertz"),
           ("content", 0.4, "pressure x conductivity 20 nm coating neck IV sweep notes about iv")]
    other = [("name", 1.0, "Neck-coating sweep"), ("tags", 0.9, ""),
             ("content", 0.4, "coating 5 nm monodisperse pack voltage")]
    # the old subsequence matcher accepted both for "iv"; whole-word / prefix matching does not
    assert smart_score("iv", big) > 0 and smart_score("iv", other) == 0
    assert smart_score("sweep", other) > smart_score("sweep", big)          # name beats content
    assert smart_score("resp", big) > 0                                      # word prefix
    assert smart_score("rspns", big) > 0                                     # abbreviation in a name word
    assert smart_score("xyz", big) == 0
    assert smart_score("", big) == 1.0
    assert smart_score("coating neck", big) > 0 and smart_score("coating iv", other) == 0   # AND
    assert smart_score("#hertz", big) > 0 and smart_score("#sweep", big) == 0               # tag scope
    assert smart_score("name:sweep", big) == 0 and smart_score("name:response", big) > 0
    assert smart_score("sweep -neck", other) == 0
    assert smart_score('"coating nm"', big) == 0 and smart_score('"20 nm coating"', big) > 0
    assert parse_query('a "b c" -d #e') == ([("any", "a"), ("any", "b c"), ("tags", "e")],
                                            [("any", "d")])
