"""Cleaner tests. Run with:  python -m tests.test_cleaner   (or pytest)"""
from pathlib import Path

from subscrub import subformats as sf
from subscrub.cleaner import CleanOptions, clean_cue, clean_doc

FIX = Path(__file__).parent / "fixtures"
O = CleanOptions()


def c(text, opts=O, fmt="srt"):
    return clean_cue(text, opts, fmt).text


def test_sound_descriptions():
    assert c("[gunshot in background]") is None
    assert c("(sighs)") is None
    assert c("Wait, [coughs] hold on.") == "Wait, hold on."
    assert c("[man speaking\nindistinctly]") is None
    assert c("{\\an8}[gunshot]") is None
    assert c("<i>[door creaks]</i>\nHello?") == "Hello?"
    assert c("[in Spanish] ¿Dónde está?") == "¿Dónde está?"
    assert c("Hey, [coughs], you.") == "Hey, you."


def test_split_across_cues():
    # description that opens in one cue and closes in the next
    assert c("Okay.\n[man speaking") == "Okay."
    assert c("indistinctly] Come in.") == "Come in."


def test_dialogue_dashes():
    assert c("- [gasps]\n- Who's there?") == "Who's there?"
    assert c("- Get down!\n- [screaming]") == "Get down!"
    assert c("- Hi.\n- [chuckles] Hello.") == "Hi.\nHello."                 # dashes always go (default)
    assert c("- One\n- Two") == "One\nTwo"
    assert c("- [grunts]\n- Hey, are you\nall right?") == "Hey, are you\nall right?"
    assert c("- it would give you\nsomething to do. -Oh, my God.") == "it would give you\nsomething to do. Oh, my God."
    assert c("He's a -- well, you know.") == "He's a -- well, you know."
    keep = CleanOptions(dialogue_dashes=False)
    assert c("- Hi.\n- [chuckles] Hello.", keep) == "- Hi.\n- Hello."
    assert c("- [gasps]\n- Who's there?", keep) == "Who's there?"
    assert c("- One\n- Two", keep) == "- One\n- Two"


def test_speaker_labels():
    assert c("JOHN: I told you.") == "I told you."
    assert c("- JOHN: Hi.\n- MARY: Hey.") == "Hi.\nHey."
    assert c("- MAN (on phone): We have a problem.") == "We have a problem."
    assert c("DR. SMITH: The results are in.") == "The results are in."
    assert c("MAN #2: Over here!") == "Over here!"
    assert c("<i>NARRATOR: Long ago,\nfar away...</i>") == "<i>Long ago,\nfar away...</i>"
    assert c(">> REPORTER: Breaking news.") == "Breaking news."
    assert c("JOSÉ: Hola.") == "Hola."
    # things that are NOT labels
    assert c("It's 10:30. We're late.") == "It's 10:30. We're late."
    assert c("I said no: absolutely not.") == "I said no: absolutely not."
    assert c("NO: I won't.") == "NO: I won't."
    assert c("Man: hello") == "Man: hello"              # mixed case off by default
    assert c("Man on TV: hello", CleanOptions(mixed_case_labels=True)) == "hello"


def test_music():
    assert c("♪ upbeat pop music playing ♪") is None
    assert c("♪♪") is None
    assert c("♪ humming ♪") is None
    # default: lyrics stay, in italics, without the ♪ marks
    assert c("♪ Walking down the street\non a sunny afternoon ♪") == "<i>Walking down the street\non a sunny afternoon</i>"
    assert c("♪ Dance with me ♪") == "<i>Dance with me</i>"
    assert c("# Walking down the street #") == "<i>Walking down the street</i>"
    assert c("- ♪ La la la ♪\n- Turn it down!") == "<i>La la la</i>\nTurn it down!"
    remove = CleanOptions(lyrics=True)
    assert c("♪ Dance with me ♪", remove) is None
    assert c("- ♪ La la la ♪\n- Turn it down!", remove) == "Turn it down!"
    keep_symbols = CleanOptions(lyrics=False, lyric_symbols=False)
    assert c("♪ Dance with me ♪", keep_symbols) == "♪ Dance with me ♪"
    assert c("♪ soft music playing ♪", keep_symbols) is None


def test_toggles_respected():
    assert c("(sighs) Fine.", CleanOptions(parentheses=False)) == "(sighs) Fine."
    assert c("[beep] Fine.", CleanOptions(brackets=False)) == "[beep] Fine."
    assert c("JOHN: Fine.", CleanOptions(speaker_labels=False)) == "JOHN: Fine."


def test_untouched_cues_identical():
    for t in ["Hello  there .", "<i>Just talking</i>", "{\\an8}Top line", "PASSIVE SCAN\nMAXIMUM RANGE"]:
        r = clean_cue(t, O, "srt")
        assert not r.changed and r.text == t


def test_ass_tags_protected():
    assert c("{\\pos(960,120)\\fad(200,200)}CALLE PRINCIPAL", fmt="ass") == "{\\pos(960,120)\\fad(200,200)}CALLE PRINCIPAL"
    assert c("{\\i1}JUAN:{\\i0} Te dije.", fmt="ass") == "{\\i1}{\\i0}Te dije."
    assert c("Espera, (tose) un momento.", fmt="ass") == "Espera, un momento."
    assert c("{\\p1}m 0 0 l 100 0 100 100{\\p0}", fmt="ass").startswith("{\\p1}")


def test_srt_roundtrip_and_renumber():
    doc = sf.load(FIX / "sdh_en.srt")
    res = clean_doc(doc, O)
    out = res.render()
    again = sf.parse(out, "srt")
    assert [x.text for x in again.cues] == [t for t in res.texts() if t]
    assert out.startswith("1\n00:00:03,000 --> 00:00:05,900\nWho's there?")
    assert res.stats.removed == 6, res.stats          # the lyric line is kept (in italics)


def test_crlf_preserved():
    doc = sf.parse("1\r\n00:00:01,000 --> 00:00:02,000\r\n[beep]\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nHi\r\n", "srt")
    out = clean_doc(doc, O).render()
    assert out == "1\r\n00:00:03,000 --> 00:00:04,000\r\nHi\r\n"


def test_vtt():
    src = ("WEBVTT\nKind: captions\n\nNOTE made by hand\n\n"
           "intro\n00:01.000 --> 00:02.000 align:start\n[music]\n\n"
           "00:03.000 --> 00:04.000\n<v Bob>BOB: Hi there</v>\n\n"
           "00:05.000 --> 00:06.000\nPlain line\n")
    doc = sf.parse(src, "vtt")
    assert len(doc.cues) == 3
    out = clean_doc(doc, O).render()
    assert "[music]" not in out and "intro" not in out
    assert "<v Bob>Hi there</v>" in out
    assert out.startswith("WEBVTT\nKind: captions\n\nNOTE made by hand\n\n00:03.000")


def test_ass_file():
    doc = sf.load(FIX / "sdh_es.ass")
    out = clean_doc(doc, O).render()
    assert "[this is a comment line, keep]" in out          # Comment: lines untouched
    assert "Style: Sign,Arial,48" in out
    assert "(disparo" not in out and "[jadea]" not in out


def test_lyrics_kept_without_symbols():
    o = CleanOptions()
    assert clean_cue("♪ I will always love you ♪", o).text == "<i>I will always love you</i>"
    assert clean_cue("<i>♪ Already italic ♪</i>", o).text == "<i>Already italic</i>"
    assert clean_cue("♪ tense music ♪", o).text is None                  # descriptions still go
    assert clean_cue("♪ x y ♪", CleanOptions(lyrics=False, lyric_symbols=False)).text == "♪ x y ♪"


if __name__ == "__main__":
    import inspect, sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
