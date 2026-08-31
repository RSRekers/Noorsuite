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
