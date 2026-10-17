"""Renaming from TMDB: parsing release names, matching (with a stand-in for TMDB), Windows-safe names.
Run with:  python -m tests.test_naming"""
from subscrub import naming
from subscrub.naming import Choice, parse, render, safe_name

MOVIES = {
    209112: dict(id=209112, title="Batman v Superman: Dawn of Justice", original_title="Batman v Superman: Dawn of Justice",
                 release_date="2016-03-23", popularity=60, runtime=152),
    268: dict(id=268, title="Batman", original_title="Batman", release_date="1989-06-21", popularity=40, runtime=126),
    335984: dict(id=335984, title="Blade Runner 2049", original_title="Blade Runner 2049", release_date="2017-10-04",
                 popularity=80, runtime=164),
    438631: dict(id=438631, title="Dune", original_title="Dune", release_date="2021-09-15", popularity=90, runtime=155),
    841: dict(id=841, title="Dune", original_title="Dune", release_date="1984-12-14", popularity=30, runtime=137),
    693134: dict(id=693134, title="Dune: Part Two", original_title="Dune: Part Two", release_date="2024-02-27",
                 popularity=95, runtime=167),
}
SHOWS = {
    34391: dict(id=34391, name="Green Lantern: The Animated Series", original_name="Green Lantern: The Animated Series",
                first_air_date="2011-11-11", popularity=20),
    60622: dict(id=60622, name="Fargo", original_name="Fargo", first_air_date="2014-04-15", popularity=50),
}
SEASONS = {
    (34391, 1): [dict(episode_number=1, name="Beware My Power (1)", runtime=22),
                 dict(episode_number=2, name="Beware My Power (2)", runtime=22),
                 dict(episode_number=9, name="...In Love and War", runtime=22)],
    (60622, 1): [dict(episode_number=1, name="The Crocodile's Dilemma", runtime=68)],
}


class FakeTMDB:
    def __init__(self):
        self.calls = 0

    def _search(self, pool, field, query, year=None):
        self.calls += 1
        q = naming._norm_tokens(query)
        out = []
        for r in pool.values():
            t = naming._norm_tokens(r[field])
            # TMDB-like: every query word must start some word of the title
            if all(any(x.startswith(w) for x in t) for w in q):
                if year is None or (r.get("release_date") or r.get("first_air_date", "")).startswith(str(year)):
                    out.append(r)
        return out

    def search_movie(self, query, year=None):
        return self._search(MOVIES, "title", query, year)

    def movie(self, i):
        return MOVIES.get(i, {})

    def search_tv(self, query, year=None):
        return self._search(SHOWS, "name", query, year)

    def season(self, i, s):
        return {"episodes": SEASONS.get((i, s), [])}


def test_parse():
    p = parse("Batman V Super Dawn of Justice 2160 HDR DV DDP.mkv")
    assert p.title == "Batman V Super Dawn of Justice" and not p.is_episode
    p = parse("Blade.Runner.2049.2017.2160p.UHD.BluRay.x265-TERMiNAL.mkv")
    assert (p.title, p.year) == ("Blade Runner 2049", 2017)
    p = parse("Green Lantern The Animated Series - S01E09 - ...In Love and War (1080p x265 EDGE2020).mkv")
    assert (p.title, p.season, p.episodes) == ("Green Lantern The Animated Series", 1, [9])
    p = parse("The.Office.US.S02E01E02.720p.mkv")
    assert p.episodes == [1, 2]
    p = parse("Show.S01E01-E03.mkv")
    assert p.episodes == [1, 2, 3]
    p = parse("Fargo (2014) - S01E01 - The Crocodile's Dilemma (1080p BluRay x265 Silence).mkv")
    assert (p.title, p.year) == ("Fargo", 2014)


def test_safe_name():
    assert safe_name("Batman v Superman: Dawn of Justice") == "Batman v Superman - Dawn of Justice"
    assert safe_name("Face/Off") == "Face-Off"
    assert safe_name("What If...?") == "What If"
    c = Choice("episode", 1, "The Walking Dead", 2010, 7, [2], [])
    assert render(c, ".mkv") == "The Walking Dead S7 E2.mkv"
    assert safe_name("Who Shaves the Barber?") == "Who Shaves the Barber"
    assert safe_name('Say "Hi"') == "Say 'Hi'"
    assert safe_name("CON") == "CON_"
    assert not safe_name("Trailing dots...").endswith(".")


def test_movie_match_and_name():
    api = FakeTMDB()
    m = naming.match(api, "Batman V Super Dawn of Justice 2160 HDR DV DDP.mkv", duration_sec=152 * 60)
    assert m.confident and m.best.tmdb_id == 209112, [(c.label(), c.score) for c in m.choices]
    assert render(m.best, ".mkv") == "Batman v Superman - Dawn of Justice.mkv"
    assert render(m.best, ".mkv", with_year=True) == "Batman v Superman - Dawn of Justice (2016).mkv"


def test_same_title_settled_by_runtime_or_year():
    api = FakeTMDB()
    m = naming.match(api, "Dune.1984.1080p.mkv", duration_sec=137 * 60)
    assert m.best.tmdb_id == 841 and m.confident
    m = naming.match(api, "Dune.mkv", duration_sec=155 * 60)                 # no year: the length decides
    assert m.best.tmdb_id == 438631
    m = naming.match(api, "Dune.mkv")                                         # nothing to go on: ask
    assert not m.confident


def test_episode_match_and_name():
    api = FakeTMDB()
    m = naming.match(api, "Green Lantern The Animated Series - S01E09 - ...In Love and War (1080p x265 EDGE2020).mkv")
    assert m.confident, [(c.label(), c.score, c.why) for c in m.choices]
    assert render(m.best, ".mkv") == "Green Lantern - The Animated Series S1 E9.mkv"
    m = naming.match(api, "Fargo (2014) - S01E01 - The Crocodile's Dilemma (1080p BluRay x265 Silence).mkv")
    assert render(m.best, ".mkv") == "Fargo S1 E1.mkv"
    # a two-part file
    c = Choice("episode", 1, "Green Lantern: The Animated Series", 2011, 1, [1, 2],
               ["Beware My Power (1)", "Beware My Power (2)"])
    assert render(c, ".mkv") == "Green Lantern - The Animated Series S1 E1-E2.mkv"


def test_unknown_title_is_not_confident():
    m = naming.match(FakeTMDB(), "Totally Unknown Home Video 2019.mkv")
    assert not m.confident and not m.choices


def test_api_key_and_errors():
    import io
    import urllib.error
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.headers.get("Authorization")
        return io.BytesIO(b'{"results": []}')
    naming.TMDB("0123456789abcdef0123456789abcdef", opener=opener).search_movie("x")
    assert "api_key=0123" in seen["url"] and seen["auth"] is None
    naming.TMDB("eyJ" + "a" * 100, opener=opener).search_movie("y")
    assert "api_key" not in seen["url"] and seen["auth"].startswith("Bearer ")

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
    try:
        naming.TMDB("bad", opener=refuse).search_movie("x")
        raise AssertionError("expected an error")
    except naming.LookupError_ as e:
        assert "key" in str(e)


def test_hard_names_parse():
    cases = [
        ("Movie.Name.(2020).1080p.mkv", "Movie Name", 2020, None),
        ("Mission.Impossible.-.Fallout.2018.mkv", "Mission Impossible - Fallout", 2018, None),
        ("Show_S01E02.mkv", "Show", None, (1, [2])),
        ("Movie Name [2020].mkv", "Movie Name", 2020, None),
        ("Show Season 1 Episode 2.mkv", "Show", None, (1, [2])),
        ("Show.S2024E01.mkv", "Show", None, (2024, [1])),
        # names that already worked must keep working
        ("Blade.Runner.2049.2017.2160p.mkv", "Blade Runner 2049", 2017, None),
        ("Spider-Man.No.Way.Home.2021.1080p.mkv", "Spider-Man No Way Home", 2021, None),
        ("Mr. Robot S01E01.mkv", "Mr. Robot", None, (1, [1])),
        ("Movie.Name.[1080p].[YTS].mkv", "Movie Name", None, None),
    ]
    for name, title, year, ep in cases:
        p = parse(name)
        assert (p.title, p.year) == (title, year), (name, p)
        assert ((p.season, p.episodes) == ep) if ep else not p.is_episode, (name, p)
    assert render(Choice("episode", 1, "Show", None, 2024, [1]), ".mkv") == "Show S2024 E1.mkv"


def test_hindi_code():
    from subscrub import media
    assert media.lang_short("hin") == "hi"
    assert media.guess_language("Movie.hi.srt") == "und"         # ".hi." still means hearing impaired
    assert media.guess_language("Movie.hin.srt") == "hin"


if __name__ == "__main__":
    import inspect
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
