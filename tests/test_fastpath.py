"""Fast path: EN/DE utterances -> exact tool calls; anything unclear must fall through to the LLM."""

import pytest

from assistant.intents.fastpath import FastPath, normalize, spoken_date, timer_reply
from assistant.intents.numbers import words_to_digits

APPS = {"spotify": "Spotify", "notepad": "Notepad", "editor": "Notepad", "chrome": "Google Chrome", "word": "Word",
        "steam": "Steam", "discord": "Discord", "rechner": "Calculator", "calculator": "Calculator"}


@pytest.fixture
def fp():
    return FastPath(lambda n: APPS.get(n.casefold()), lambda: ["Jarvis"], lambda: "en")


@pytest.mark.parametrize("text, tool, args", [
    ("Jarvis, set the volume to forty", "volume", {"level": 40}),
    ("Lautstärke auf fünfundzwanzig Prozent", "volume", {"level": 25}),
    ("kannst du bitte die Lautstärke auf 30 stellen", "volume", {"level": 30}),
    ("lauter", "volume", {"change": 10}),
    ("turn it down a bit", "volume", {"change": -10}),
    ("volume up by 20", "volume", {"change": 20}),
    ("Lautstärke um 15 Prozent senken", "volume", {"change": -15}),
    ("mute", "volume", {"mute": True}),
    ("Ton aus", "volume", {"mute": True}),
    ("ton wieder an", "volume", {"mute": False}),
    ("pause", "media", {"action": "pause"}),
    ("Musik pausieren", "media", {"action": "pause"}),
    ("next song", "media", {"action": "next"}),
    ("nächstes Lied", "media", {"action": "next"}),
    ("vorheriger Titel", "media", {"action": "previous"}),
    ("weiter", "media", {"action": "play"}),
    ("what's playing", "media", {"action": "status"}),
    ("Hey Jarvis, play Bohemian Rhapsody", "play_music", {"query": "bohemian rhapsody"}),
    ("spiel Queen auf Spotify", "play_music", {"query": "queen", "app": "spotify"}),
    ("ich will Taylor Swift hören", "play_music", {"query": "taylor swift"}),
    ("play something by Daft Punk", "play_music", {"query": "daft punk"}),
    ("set a timer for 5 minutes", "timer", {"action": "set_timer", "seconds": 300}),
    ("stelle einen Timer auf zehn Minuten", "timer", {"action": "set_timer", "seconds": 600}),
    ("fünf minuten timer", "timer", {"action": "set_timer", "seconds": 300}),
    ("timer für eine halbe Stunde", "timer", {"action": "set_timer", "seconds": 1800}),
    ("cancel the timer", "timer", {"action": "cancel", "all": True}),
    ("lock the pc", "power", {"action": "lock"}),
    ("PC sperren", "power", {"action": "lock"}),
    ("switch to desktop 2", "desktop", {"action": "switch", "target": "2"}),
    ("wechsle zum nächsten Desktop", "desktop", {"action": "switch", "target": "next"}),
    ("open spotify", "open_app", {"name": "Spotify"}),
    ("öffne den Editor", "open_app", {"name": "Notepad"}),
    ("mach Steam auf", "open_app", {"name": "Steam"}),
    ("Rechner öffnen", "open_app", {"name": "Calculator"}),
    ("close notepad", "window", {"action": "close", "target": "Notepad"}),
    ("schließe Discord", "window", {"action": "close", "target": "Discord"}),
    ("close this window", "window", {"action": "close"}),
    ("brightness 60", "brightness", {"level": 60}),
    ("heller", "brightness", {"change": 15}),
])
def test_matches(fp, text, tool, args):
    fi = fp.match(text)
    assert fi is not None, text
    assert (fi.tool, fi.args) == (tool, args)


@pytest.mark.parametrize("text", [
    "open the downloads folder", "open report.pdf", "open photoshop", "summarize my last email",
    "lösche die Datei test.txt", "what's the weather like", "close the file", "set the volume to 400",
    "play", "remind me sometime to do it",
])
def test_falls_through_or_safe(fp, text):
    fi = fp.match(text)
    if text == "play":
        assert fi is not None and fi.args == {"action": "play"}   # bare "play" is a media command
    else:
        assert fi is None, (text, fi)


def test_local_answers_and_stop(fp):
    assert fp.match("what time is it").answer.startswith("It's")
    assert fp.match("wie spät ist es").answer.startswith("It's")      # reply language follows the config
    de = FastPath(lambda n: None, lambda: ["Jarvis"], lambda: "de")
    assert de.match("wie spät ist es").answer.endswith("Uhr.")
    assert de.match("welcher tag ist heute").answer.startswith("Heute ist")
    assert fp.match("Jarvis, stop").action == "cancel"
    assert fp.match("abbrechen").action == "cancel"


def test_reminders(fp):
    fi = fp.match("remind me in 20 minutes to check the oven")
    assert fi.tool == "timer" and fi.args["action"] == "set_reminder" and fi.args["label"] == "check the oven"
    fi = fp.match("erinnere mich um 17 Uhr daran, Mama anzurufen")
    assert fi.args["at"].endswith("17:00:00") and "mama anzurufen" in fi.args["label"]


def test_normalize_and_numbers():
    assert normalize("Hey JARVIS, could you please turn it up, please!", ["Jarvis"]) == "turn it up"
    assert words_to_digits("einundzwanzig und dreiundvierzig") == "21 und 43"
    assert words_to_digits("forty-five percent") == "45 percent"
    assert words_to_digits("hundert prozent") == "100 prozent"
    assert "ß" in normalize("schließe das", [])


def test_reply_texts():
    import datetime as dt

    assert timer_reply(330, "en") == "Timer set for 5 minutes 30 seconds."
    assert timer_reply(3600, "de") == "Timer für 1 Stunde gestellt."
    assert spoken_date(dt.date(2026, 11, 6), "de") == "Heute ist Freitag, der 6. November."
    assert spoken_date(dt.date(2026, 11, 3), "en") == "Today is Tuesday, November 3rd."
