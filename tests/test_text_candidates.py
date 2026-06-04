from grasp.computer import text_candidates


def _w(t, l, tp, w=20, h=14, line=0, wn=0):
    return {"t": t, "l": l, "tp": tp, "w": w, "h": h, "key": (0, 0, line), "wn": wn}


# a toolbar row + a multi-word button on the next line
WORDS = [
    _w("Highlight", 100, 10, line=0, wn=0),
    _w("Measure", 220, 10, line=0, wn=1),
    _w("Occlude", 320, 10, line=0, wn=2),
    _w("Save", 100, 40, line=1, wn=0),
    _w("copy", 150, 40, line=1, wn=1),
    _w("Find", 300, 40, line=1, wn=2),
    _w("all", 350, 40, line=1, wn=3),
    _w("Measure", 500, 70, line=2, wn=0),   # a second "Measure" lower down
]


def test_single_word_match_center():
    c = text_candidates(WORDS, "Occlude")
    assert len(c) == 1
    box = c[0][2]
    assert box == (320, 10, 340, 24)         # exact word box


def test_multi_word_phrase_matches_consecutive_run():
    c = text_candidates(WORDS, "Save copy")
    assert len(c) == 1
    assert c[0][3] == "Save copy"
    assert c[0][2] == (100, 40, 170, 54)     # union of the two words' boxes


def test_find_all_phrase():
    c = text_candidates(WORDS, "Find all")
    assert len(c) == 1 and c[0][3] == "Find all"


def test_occurrence_ordering_for_duplicates():
    c = text_candidates(WORDS, "Measure")
    assert len(c) == 2
    # reading order: the toolbar one (top=10) before the lower one (top=70)
    assert c[0][2][1] == 10 and c[1][2][1] == 70


def test_whole_word_excludes_substring():
    words = [_w("Highlighter", 10, 10)]
    assert text_candidates(words, "Highlight", whole=False)      # substring matches
    assert not text_candidates(words, "Highlight", whole=True)   # exact required
