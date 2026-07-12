from dota2vod.segments import group, pick_team_names, refine_boundary, smooth


def make_samples(spec: str, step: float = 10.0):
    """'GG--GG' -> [(0, True), (10, True), (20, False), ...]"""
    return [(i * step, c == "G") for i, c in enumerate(spec)]


def test_smooth_kills_isolated_flips():
    samples = make_samples("GG-GG--G--")
    flags = [f for _, f in smooth(samples)]
    assert flags == [True, True, True, True, True, False, False, False, False, False]


def test_group_merges_pauses_and_drops_short_blips():
    # Game with a 20s pause in it, then a short replay blip later.
    samples = make_samples("--GGGG--GGGG------G-------", step=10)
    segs = group(samples, merge_gap=30, min_duration=60)
    assert len(segs) == 1
    assert segs[0].start == 20
    assert segs[0].end == 110


def test_group_keeps_separate_games():
    samples = make_samples("-GGGG------GGGG-", step=60)
    segs = group(samples, merge_gap=180, min_duration=120)
    assert len(segs) == 2


def test_refine_boundary_finds_transition():
    true_boundary = 137.0  # in-game starts here
    probe = lambda t: t >= true_boundary
    found = refine_boundary(lo=120, hi=180, lo_in_game=False, probe=probe, precision=1.0)
    assert abs(found - true_boundary) <= 1.0
    # End transition: in-game up to 411, then panel.
    probe_end = lambda t: t < 411.0
    found_end = refine_boundary(lo=390, hi=450, lo_in_game=True, probe=probe_end, precision=1.0)
    assert abs(found_end - 411.0) <= 1.0


def test_pick_team_names_majority_vote():
    votes = [
        ("LIQUID", "SPIRIT"),
        ("LIQUID", "SPIRIT"),
        ("LIOUID", "SPIRIT"),
        ("", "SP1RIT"),
    ]
    left, right = pick_team_names(votes)
    assert left == "LIQUID"
    assert right == "SPIRIT"


def test_pick_team_names_empty_votes():
    assert pick_team_names([]) == ("Unknown", "Unknown")
