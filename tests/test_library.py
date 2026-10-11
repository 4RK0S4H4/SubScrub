"""Regression cases taken from real SDH tracks in the user's library (Oct 2026 subtitle dump).

Each case is (input cue, expected output) with the default options:
dialogue dashes removed, lyrics kept in italics without ♪, on-screen text kept.
Run with:  python -m tests.test_library
"""
from subscrub import subformats as sf
from subscrub.cleaner import CleanOptions, clean_cue, clean_doc

O = CleanOptions()

CASES = [
    # --- bracket speaker IDs and sounds (Avatar, Project Hail Mary, Daredevil, The Expanse)
    ("[Tsireya] Thank you,\nGreat Mother.", "Thank you,\nGreat Mother."),
    ("- [Ta'nok wails]\n- [sniffles, sighs]", None),
    ("{\\an8}- [♪ fanfare plays]\n- [Mayor Kingpin] It's me again,", "{\\an8}It's me again,"),
    ("<i>[Carol] [over phone] Um...\none second, please.</i>", "<i>Um...\none second, please.</i>"),
    ("- and the Maulers, and...\n- <i>[Cecil] It doesn't matter.</i>", "and the Maulers, and...\n<i>It doesn't matter.</i>"),
    ("[Naomi]\nYou know I've got your back.", "You know I've got your back."),
    ('[♪ Radiohead: "Karma Police"]', None),
    ('["American Wedding"\nby Gogol Bordello playing]', None),
    ("[in deep, raspy male voice]\n<i>Why is a schoolteacher</i>", "<i>Why is a schoolteacher</i>"),
    # --- parentheses (Succession, Lanterns, Flow, Guardians)
    ("- Oh, smooches. (SMOOCHES)\n- (SMOOCHES)", "Oh, smooches."),
    ("Well, I think... (CLICKS TONGUE)\n...I think I liked it.", "Well, I think...\n...I think I liked it."),
    ("- (RING HUMS SHARPLY)\n- AUTOMATED VOICE:\n<i>Ring charge, five percent.</i>", "<i>Ring charge, five percent.</i>"),
    ("(MUSIC FADING UP,\n\"BRANDY (YOU'RE A FINE GIRL)\" PLAYING)", None),
    ("(Students) Good morning!", "Good morning!"),
    # --- speaker labels in all their forms
    ("NAOMI:Take it easy.", "Take it easy."),                                    # The Expanse: no space
    ("[GRUNTS]\nNAOMI:Take it easy.", "Take it easy."),
    ("MAN [ON TV]:And why won't we help Eros?", "And why won't we help Eros?"),
    ("Nice.HOLDEN: Shut up.", "Nice.\nShut up."),                                # glued: a lost line break
    ("Who's that?BOUILLOTTE DEALER:\nEarth, Mars.", "Who's that?\nEarth, Mars."),
    ("NAOMI: Go! HOLDEN: Pressurizing.", "Go! Pressurizing."),
    ("- What if you got her\nsomething nice? -IAN: No, no.", "What if you got her\nsomething nice? No, no."),  # Obsession
    ("- HOST: Thanks for coming out\ntonight. -NIKKI and IAN: Oh.", "Thanks for coming out\ntonight. Oh."),
    ('- WILLIAM "BILLY" MACON:\nYeah, don\'t-- don\'t answer that.\n- (DOOR CLOSES)', "Yeah, don't-- don't answer that."),
    ("McDOWELL: Go.", "Go."),
    ('"MAN: Wait!', "Wait!"),                                                    # Thor: OCR'd quote
    ("COMPUTER;\nVoice activation required.", "Voice activation required."),     # Thor: ; for :
    ("JOHN STEWART JUNIOR:\nWhat did the Guardians say?", "What did the Guardians say?"),
    ("SARAH [laughs]: Fine.", "Fine."),
    ("and drink their rivers dry?\nALL: MMC!", "and drink their rivers dry?\nMMC!"),
    # --- things that must NOT be touched
    ("CHAINSAW MAN - THE MOVIE:\nREZE ARC", "CHAINSAW MAN - THE MOVIE:\nREZE ARC"),  # title card
    ("PASSIVE SCAN\nMAXIMUM RANGE", "PASSIVE SCAN\nMAXIMUM RANGE"),            # on-screen text
    ("THE U.N. BUILDING\nNEW YORK CITY", "THE U.N. BUILDING\nNEW YORK CITY"),
    ("Warning: the following\nimages are graphic.", "Warning: the following\nimages are graphic."),
    ("Size: unknown. Speed: unknown.", "Size: unknown. Speed: unknown."),
    ("Our PGN is just gonna\ncrush this.", "Our PGN is just gonna\ncrush this."),
    ("We have the FBI: they know.", "We have the FBI: they know."),
    ("Uh, just FYI, lizard guy's here too.", "Uh, just FYI, lizard guy's here too."),
    ("He's a -- well, you know.", "He's a -- well, you know."),
    # --- music vs lyrics
    ("♪ (MUSIC FADES) ♪", None),
    ("- ♪ (SUSPENSEFUL MUSIC PLAYING) ♪", None),
    ("♪♪ [tranquil]", None),
    ("[♪ dramatic music playing]", None),
    ("♪ ♪", None),
    ("♪ (“SUCCESSION”\nTHEME MUSIC PLAYING) ♪", None),
    ("<i>♪ Hold still right before we crash\n'Cause we both know how this ends… ♪</i>",
     "<i>Hold still right before we crash\n'Cause we both know how this ends…</i>"),          # "ends" is a lyric here
    ("<i>♪ 'Cause when the music starts\nWe all get together ♪</i>",
     "<i>'Cause when the music starts\nWe all get together</i>"),
    ("[singers] <i>♪ Join the fight\nAnd even the score ♪</i>", "<i>Join the fight\nAnd even the score</i>"),
    ("[Penguin]\n<i>♪ I know I've done you wrong ♪</i>", "<i>I know I've done you wrong</i>"),
    ("♪ Yeah, I think I played it\npretty cool with you, babe... ♪", "<i>Yeah, I think I played it\npretty cool with you, babe...</i>"),
    ("We dream as one ♪", "<i>We dream as one</i>"),
    # --- on-screen text notes
    ("[sign reads: EXIT]", "EXIT"),
    ('[text on screen: "Three days later"]', "Three days later"),
]


def test_library_cases():
    bad = []
    for src, want in CASES:
        got = clean_cue(src, O).text
        if got != want:
            bad.append(f"{src!r}\n   want {want!r}\n   got  {got!r}")
    assert not bad, "\n" + "\n".join(bad)


def _doc(texts):
    lines = []
    for i, t in enumerate(texts):
        lines += [str(i + 1), f"00:00:{i:02d},000 --> 00:00:{i:02d},900", t, ""]
    return sf.parse("\n".join(lines), "srt")


def test_mixed_case_labels_detected_per_file():
    # Spider-Man: Far From Home's SDH track writes names like "Harrington:" all the way through
    doc = _doc(["Harrington: Follow me, kids!", "- Okay?\n- Harrington: Look at this, guys.", "Mj: Whoa, cool.",
                "Mj: That doesn't look good.", "Beck: Peter, listen to me.", "Beck: Pause.", "Happy: Yeah, no.",
                "Betty: Go! Peter: Just go!", "Woman [over pa]: Good afternoon,\nthis is your captain speaking.",
                "Warning: the following images are graphic.", "Peter: Hey."])
    res = clean_doc(doc, O)
    t = res.texts()
    assert t[0] == "Follow me, kids!" and t[1] == "Okay?\nLook at this, guys." and t[2] == "Whoa, cool."
    assert t[7] == "Go! Just go!"
    assert t[8] == "Good afternoon,\nthis is your captain speaking."
    assert t[9] == "Warning: the following images are graphic."
    # ...but a normal file keeps "Name:" lines
    doc = _doc(["Warning: the following images are graphic.", "Size: unknown. Speed: unknown.", "Okay."])
    assert clean_doc(doc, O).texts()[:2] == ["Warning: the following images are graphic.", "Size: unknown. Speed: unknown."]


def test_ocr_damaged_tracks():
    # Donnie Darko: ♪ came out of OCR as j"
    doc = _doc(['j" the killing moon', 'j" will come too soon', 'j" up in your arms\ntoo late to beg you',
                "(J' echo & the bunnymen:\n\"The killing moon\")", "Hello."])
    t = clean_doc(doc, O).texts()
    assert t[0] == "<i>the killing moon</i>" and t[2] == "<i>up in your arms\ntoo late to beg you</i>"
    assert t[3] is None and t[4] == "Hello."
    # Spider-Man: ♪ came out as a leading and trailing "I", and "I" came out as "["
    doc = _doc(["I hey, little girl\nI want to be your boyfriend I", "I stop your messin' around I",
                "I can't seem to get my mind off of you I", "- Edith?\n- Hello, Peter. How can [help you?",
                "[M1", "[M1", "I said so, didn't I", "Neither do I"])
    res = clean_doc(doc, O)
    t = res.texts()
    assert t[0] == "<i>hey, little girl\nI want to be your boyfriend</i>"
    assert t[3] == "Edith?\nHello, Peter. How can I help you?"
    assert t[4] is None
    assert t[6] == "I said so, didn't I" and t[7] == "Neither do I"
    assert res.notes.ocr_suspect and 3 in res.flagged()


def test_green_lantern_style_file():
    # Green Lantern TAS (.eng.srt): bare sound words, and off-screen voices wrapped in 'single quotes'
    doc = _doc(["(M'Ten)\n'Alien damaged ship,\ncan you hear me in there?'", "There's no cause\nfor alarm.",
                "whoosh", "whoosh whoosh whoosh", "pew pew pew", "ka-boom", "(male #1)\n'Wrong.'",
                "'The viceroy of Demrakk 7 claims\nthat when we assigned you'", "'you punched him in the face.'",
                "(Kilowog)\n'Salaak, whose ring is this? '", "'Green Lantern M'Ten.'", "Company's comin'.",
                "'Cause I said so.", "'Enough even for you.'", "boom", "I said boom!"])
    res = clean_doc(doc, O)
    t = res.texts()
    assert res.notes.quote_voices
    assert t[0] == "<i>Alien damaged ship,\ncan you hear me in there?</i>"
    assert t[1] == "There's no cause\nfor alarm."
    assert t[2] is None and t[3] is None and t[4] is None and t[5] is None and t[14] is None
    assert t[6] == "<i>Wrong.</i>"
    assert t[7] == "<i>The viceroy of Demrakk 7 claims\nthat when we assigned you</i>"
    assert t[9] == "<i>Salaak, whose ring is this?</i>"
    assert t[10] == "<i>Green Lantern M'Ten.</i>"
    assert t[11] == "Company's comin'."
    assert t[12] == "'Cause I said so."
    assert t[15] == "I said boom!"
    # a normal file's 'Cause lines and quotes are left alone
    plain = _doc(["'Cause I said so.", "He said 'no'.", "'Course not.", "Fine."])
    assert clean_doc(plain, O).texts()[:3] == ["'Cause I said so.", "He said 'no'.", "'Course not."]


def test_confidence():
    clean = _doc(["[door slams]", "JOHN: Hi.", "- [gasps]\n- Who's there?", "Fine."])
    assert clean_doc(clean, O).confidence == "high"
    shaky = _doc(["It costs 5:30 (approx.) today.", "Fine."])
    res = clean_doc(shaky, O)
    assert res.confidence == "check" and res.flagged() == [0]
    res.overrides[0] = "orig"
    assert res.confidence == "high" and res.texts()[0] == "It costs 5:30 (approx.) today."


def test_plain_track_cross_check():
    sdh = _doc(["[door slams]", "It costs 5:30 (about five dollars) today.", "JOHN: Hi."])
    plain = _doc(["", "It costs 5:30 (about five dollars) today.", "Hi."])
    plain.cues[0].text = "Mm."
    res = clean_doc(sdh, O, plain=plain)
    assert 1 in res.flagged() and any("regular English subtitles" in w for w in res.results[1].why)
    assert 2 not in res.flagged()


if __name__ == "__main__":
    import inspect
    import sys
    fns = [f for n, f in sorted(globals().items()) if n.startswith("test_") and inspect.isfunction(f)]
    for f in fns:
        f()
        print("ok ", f.__name__)
    print(f"{len(fns)} tests passed")
    sys.exit(0)
