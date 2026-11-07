"""Milestone 5: onboarding questions, answer interpretation and the wizard state machine (no GUI)."""

import pytest

from assistant.onboarding.env import WizardEnv
from assistant.onboarding.interpret import clean_name, interpret, parse_amount, parse_bool
from assistant.onboarding.questions import BY_ID, QUESTIONS, label, routine_steps, spoken
from assistant.onboarding.wizard import Wizard
from assistant.permissions.policy import Level, Risk


@pytest.fixture
def env(ts):
    system, *_ = ts
    return WizardEnv(ctx=system.ctx, policy=system.policy, routines=system.services["routines"],
                     app_names=lambda: [a.name for a in system.services["apps"].apps()], vram_mb=8188,
                     first_run=True, user_name_guess=lambda: "Lennox", language_guess=lambda: "de")


def test_spec_topics_are_covered():
    ids = [q.id for q in QUESTIONS]
    assert len(ids) >= 15 and len(set(ids)) == len(ids)
    for needed in ("assistant_name", "wake_test", "user_name", "language", "voice_replies", "voice", "personality",
                   "answer_length", "microphone", "music_app", "browser", "default_apps", "folders", "permissions",
                   "confirm_actions", "desktop_rules", "autostart", "hotkeys", "silence_timeout", "game_mode",
                   "model", "save_conversations", "retention", "voice_lock", "routines"):
        assert needed in ids, needed


def test_every_question_has_both_languages_and_a_working_default(env):
    for q in QUESTIONS:
        assert label(q.title, "en") and label(q.title, "de"), q.id
        q.default(env)                                    # must not raise
        if q.options is not None:
            opts = q.options(env)
            assert opts and all(label(o.label, "en") and label(o.label, "de") for o in opts), q.id
        assert spoken(q, env)


def test_walk_through_with_typed_answers(env):
    w = Wizard(env)
    cfg = env.ctx.config
    assert w.current.id == "language" and w.value() == "de"          # guessed from Windows
    assert w.handle_text("English").action == "answer" and cfg.data.general.language == "en"
    assert w.current.id == "assistant_name"
    assert w.handle_text("Call yourself Nova.").action == "answer" and cfg.data.general.assistant_name == "Nova"
    assert w.current.id == "wake_test"
    w.skip()
    assert w.current.id == "user_name" and w.value() == "Lennox"
    w.handle_text("Just call me Lenny")
    assert cfg.data.general.user_name == "Lenny"
    w.answer({"about": "I build robots", "location": "Kiel"})
    assert cfg.data.profile.about == "I build robots" and cfg.data.profile.location == "Kiel"
    w.handle_text("no")                                               # voice replies off ...
    assert cfg.data.voice.tts_enabled is False
    assert w.current.id == "personality"                              # ... so the voice question is skipped
    w.handle_text("the third one")
    assert cfg.data.general.personality == "witty"
    w.handle_text("detailed please")
    assert cfg.data.general.answer_length == "detailed"
    assert w.current.id == "microphone"
    w.skip()
    assert w.current.id == "music_app" and w.value() == "Spotify"    # detected
    w.skip()
    w.handle_text("Chrome")
    assert cfg.data.apps.browser == "Google Chrome"
    w.handle_text("Outlook for mail and Discord for chat")
    aliases = cfg.data.apps.aliases
    assert aliases["mail"] == "Outlook" and aliases["chat"] == "Discord"
    assert w.current.id == "folders"
    w.answer([{"path": "D:\\Projects", "access": "full", "note": ""}])
    assert env.policy.policy.folders[0].path == "D:\\Projects"
    perms = w.value()
    perms["input"] = "allow"
    w.answer(perms)
    assert env.policy.policy.level("input") is Level.ALLOW
    w.handle_text("all")
    assert Risk.POWER in env.policy.policy.always_confirm
    w.handle_text("yes")                                              # separate desktop
    assert w.current.id == "desktop_rules" and w.value()["spotify"] == "assistant"
    w.answer({"spotify": "assistant", "discord": "assistant", "steam": "current"})
    assert cfg.data.desktops.rules["discord"] == "assistant"
    w.skip()                                                          # autostart: default yes on first run
    assert cfg.data.general.autostart is True
    w.answer({"push_to_talk": "ctrl+shift+space", "panic": "ctrl+alt+escape", "mode": "hold"})
    assert cfg.data.hotkeys.push_to_talk == "ctrl+shift+space" and cfg.data.voice.push_to_talk_mode == "hold"
    w.handle_text("one and a half seconds")
    assert cfg.data.voice.silence_timeout_s == 1.5
    w.handle_text("yes")
    w.handle_text("fast")
    assert cfg.data.llm.profile == "fast"
    w.handle_text("yes")
    w.handle_text("one month")
    assert cfg.data.privacy.retention_days == 30
    assert w.current.id == "voice_lock"
    assert w.handle_text("no thanks").action == "answer" and cfg.data.voice.voice_lock is False
    assert w.current.id == "routines"
    items = w.value()
    gaming = next(i for i in items if i["key"] == "gaming")
    assert gaming["apps"] == "Steam, Discord"                       # installed apps filled in
    gaming["enabled"] = True
    w.answer(items)
    r = env.routines.get("Gaming mode")
    assert r is not None and r.steps[0] == {"tool": "say", "args": {"text": "Gaming mode activated. Have fun!"}}
    assert r.steps[1] == {"tool": "open_app", "args": {"name": "Steam"}}
    assert w.at_end
    w.finish()
    assert cfg.data.general.onboarding_done is True


def test_back_skip_and_conditions(env):
    w = Wizard(env)
    w.skip()                                       # language -> default "de" applied
    assert env.ctx.config.data.general.language == "de"
    assert w.current.id == "assistant_name"
    assert w.back() and w.current.id == "language"
    assert not w.back()                            # nothing before the first question
    w.skip()
    w.skip()
    w.skip()
    assert w.current.id == "user_name"
    n, total = w.progress()
    assert n == 4 and total >= 24
    env.ctx.config.set("privacy.save_conversations", False)
    assert not w.visible(BY_ID["retention"])
    env.ctx.config.set("desktops.enabled", False)
    assert not w.visible(BY_ID["desktop_rules"])


def test_finish_early_applies_the_remaining_defaults(env):
    w = Wizard(env)
    w.handle_text("English")
    w.finish()
    cfg = env.ctx.config.data
    assert cfg.general.onboarding_done and cfg.general.autostart is True and cfg.general.user_name == "Lennox"
    assert cfg.apps.music_app == "Spotify"


def test_validation_errors(env):
    w = Wizard(env)
    w.skip()
    err = w.answer("x")
    assert err is not None and w.current.id == "assistant_name"
    assert w.answer("A very long name with many words") is not None
    w.answer("Jarvis")
    q = BY_ID["hotkeys"]
    assert q.validate(env, {"push_to_talk": "ctrl+alt+x", "panic": "ctrl+alt+x"}) is not None
    assert q.validate(env, {"push_to_talk": "ctrl+alt+x", "panic": ""}) is not None
    assert q.validate(env, {"push_to_talk": "ctrl+nonsense", "panic": "ctrl+alt+escape"}) is not None
    assert q.validate(env, {"push_to_talk": "", "panic": "ctrl+alt+escape"}) is None


@pytest.mark.parametrize("text,expected", [
    ("Call me Lennox.", "Lennox"), ("My name is anna-lena", "Anna-Lena"), ("Ich heiße Jürgen.", "Jürgen"),
    ("Nenn mich einfach Max", "Einfach Max"), ("Friday", "Friday"), ("you can call me Sam please", "Sam"),
])
def test_clean_name(text, expected):
    assert clean_name(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("1.5", 1.5), ("1,5 Sekunden", 1.5), ("one and a half seconds", 1.5), ("eineinhalb", 1.5),
    ("zwei Komma fünf", 2.5), ("two seconds", 2.0), ("anderthalb Sekunden", 1.5), ("half a second", 0.5),
    ("no idea", None),
])
def test_parse_amount(text, expected):
    assert parse_amount(text) == expected


def test_interpretations(env):
    q = BY_ID["personality"]
    assert interpret(q, "Witzig bitte", env).value == "witty"           # German label while UI is English
    assert interpret(q, "the second one", env).value == "professional"
    assert interpret(q, "something sarcastic", env).action == "unclear"
    assert interpret(q, "weiter", env).action == "skip"
    assert interpret(q, "zurück", env).action == "back"
    assert interpret(q, "say that again", env).action == "repeat"
    assert interpret(BY_ID["language"], "Deutsch", env).value == "de"
    b = BY_ID["voice_replies"]
    assert interpret(b, "yes please", env).value is True and interpret(b, "Nein.", env).value is False
    assert interpret(b, "okay", env).value is True                      # yes, not "skip"
    n = BY_ID["silence_timeout"]
    assert interpret(n, "ten seconds", env).value == 3.0                # clamped to the range
    m = BY_ID["confirm_actions"]
    assert interpret(m, "none", env).value == []
    assert interpret(m, "keine", env).value == []
    v = BY_ID["voice"]
    assert interpret(v, "male two", env).value == "M2"
    assert interpret(BY_ID["music_app"], "I use Deezer", env).value == "Deezer"   # free text allowed
    assert interpret(BY_ID["wake_test"], "ready", env).action == "start"
    assert interpret(BY_ID["voice_lock"], "yes", env).action == "start"
    assert interpret(BY_ID["folders"], "okay", env).action == "skip"
    assert interpret(BY_ID["model"], "schnell", env).value == "fast"
    assert interpret(BY_ID["retention"], "forever", env).value == 0


def test_parse_bool_words():
    assert parse_bool("an") is True and parse_bool("aus") is False and parse_bool("lieber nicht") is False
    assert parse_bool("maybe") is None


def test_routine_template_steps():
    steps = routine_steps({"key": "night", "apps": "", "volume": None})
    assert steps == [{"tool": "media", "args": {"action": "pause"}}, {"tool": "power", "args": {"action": "lock"}}]
    steps = routine_steps({"key": "gaming", "apps": "Steam, Discord", "volume": "40"})
    assert [s["tool"] for s in steps] == ["say", "open_app", "open_app", "volume"] and steps[3]["args"]["level"] == 40
    assert steps[0]["args"]["text"] == "Gaming mode activated. Have fun!"
    de = routine_steps({"key": "gaming", "apps": "Steam", "volume": None}, "de")
    assert de[0] == {"tool": "say", "args": {"text": "Spielmodus aktiviert. Viel Spaß!"}}


def test_text_wizard(env):
    from assistant.onboarding.text import run_text_wizard

    answers = iter(["2", "Nova", "", "Sam", "", "",            # language .. profile (2 fields)
                    "no", "witty", "", "", "", "",                # voice replies off -> no voice question
                    "", "", "", "", "",                           # default apps (5 fields)
                    "add D:\\Work full", "",                     # folders: add, then keep
                    "input=allow", "", "", "", "",                # permissions .. autostart
                    "ptt=ctrl+alt+p panic=ctrl+alt+escape", "2,5", "", "fast", "", "", "", "1 4"])
    out: list[str] = []
    run_text_wizard(Wizard(env), lambda prompt: next(answers), lambda *a: out.append(" ".join(map(str, a))))
    c = env.ctx.config.data
    assert c.general.language == "de" and c.general.assistant_name == "Nova" and c.general.user_name == "Sam"
    assert c.voice.tts_enabled is False and c.general.personality == "witty"
    assert env.policy.policy.folders[0].path == "D:\\Work"
    assert env.policy.policy.level("input") is Level.ALLOW
    assert c.hotkeys.push_to_talk == "ctrl+alt+p" and c.voice.silence_timeout_s == 2.5
    assert c.llm.profile == "fast" and c.general.onboarding_done
    assert {r.name for r in env.routines.all()} == {"Spielmodus", "Gute Nacht"}
    assert out[-1].strip() == "Einrichtung gespeichert."


def test_folder_answer_rebuilds_the_path_rules(env):
    calls = []
    env.policy_changed = lambda: calls.append(True)
    BY_ID["folders"].apply(env, [{"path": "D:\\Work", "access": "full", "note": ""}])
    assert calls == [True]
