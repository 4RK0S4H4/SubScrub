"""A stand-in for TMDB with the sample library's titles (tests/make_samples.py --library), so screenshots and the
scripted QA run show real-looking new names without a network or a key. Never used unless asked for
(tools/ui_screenshots.py, or the helper's --demo-tmdb)."""
from . import naming

MOVIES = {693134: dict(id=693134, title="Dune: Part Two", original_title="Dune: Part Two", release_date="2024-02-27",
                       popularity=95, runtime=167),
          78: dict(id=78, title="Blade Runner", original_title="Blade Runner", release_date="1982-06-25",
                   popularity=60, runtime=117)}
SHOWS = {63639: dict(id=63639, name="The Expanse", original_name="The Expanse", first_air_date="2015-12-14",
                     popularity=60),
         95396: dict(id=95396, name="Severance", original_name="Severance", first_air_date="2022-02-17", popularity=80),
         96074: dict(id=96074, name="Slow Horses", original_name="Slow Horses", first_air_date="2022-04-01",
                     popularity=50),
         96075: dict(id=96075, name="Slow Horses: Inside", original_name="Slow Horses: Inside",
                     first_air_date="2022-04-01", popularity=49),
         231003: dict(id=231003, name="Planet Earth III", original_name="Planet Earth III",
                      first_air_date="2023-10-22", popularity=30)}
SEASONS = {(63639, 1): [dict(episode_number=3, name="Remember the Cant", runtime=44)],
           (95396, 2): [dict(episode_number=4, name="Woe's Hollow", runtime=50)],
           (96074, 4): [], (96075, 4): [],
           (231003, 1): [dict(episode_number=2, name="Ocean", runtime=58)]}


class FakeTMDB:
    def __init__(self, *_a, **_k):
        pass

    def _search(self, pool, field, query, year=None):
        q = naming._norm_tokens(query)
        return [r for r in pool.values() if all(any(x.startswith(w) for x in naming._norm_tokens(r[field])) for w in q)
                and (year is None or (r.get("release_date") or r.get("first_air_date", "")).startswith(str(year)))]

    def search_movie(self, query, year=None):
        return self._search(MOVIES, "title", query, year)

    def movie(self, i):
        return MOVIES.get(i, {})

    def search_tv(self, query, year=None):
        return self._search(SHOWS, "name", query, year)

    def season(self, i, s):
        return {"episodes": SEASONS.get((i, s), [])}
