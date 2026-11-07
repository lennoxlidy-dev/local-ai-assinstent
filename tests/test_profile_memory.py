"""Milestone 5: the profile in the system prompt, remembered facts, memory fast paths."""

from scripted_llm import ScriptedLLM, answer

from assistant.core.assistant import Assistant
from assistant.llm.prompts import FACT_CHARS, Profile, profile_block, system_prompt
from assistant.storage.db import Database
from assistant.storage.memory import MemoryStore


def test_profile_block_contains_profile_and_budgeted_facts():
    facts = [f"Fact number {i} about something the user said a while ago" for i in range(60)]
    p = Profile("Nova", "Lennox", "en", facts=facts, about="Software developer, likes jazz", location="Hamburg")
    block = profile_block(p, "en")
    assert "About the user: Software developer, likes jazz" in block
    assert "The user lives in: Hamburg" in block
    assert "Call the user Lennox." in block
    assert "Fact number 0 " in block and "Fact number 59" not in block      # newest first, budget applies
    assert "use the recall tool" in block
    assert len(block) < FACT_CHARS + 600
    de = system_prompt(Profile("Nova", "Lennox", "de", facts=["Mag Kaffee"], about="Arzt"))
    assert "Über den Nutzer: Arzt" in de and "- Mag Kaffee" in de and "Nenne den Nutzer Lennox." in de
    empty = system_prompt(Profile())
    assert "About the user" not in empty and "What you know" not in empty


def test_near_duplicate_facts_are_merged_but_different_facts_kept():
    m = MemoryStore(Database(":memory:"))
    a = m.add("My sister's name is Lea")
    b = m.add("my sisters name is Lea.")
    assert a.id == b.id and m.count() == 1 and m.all()[0].text == "my sisters name is Lea."
    m.add("My sister's name is Mia")            # a correction is a different fact (the LLM forgets the old one)
    m.add("My brother's birthday is May 3")
    m.add("My sister's birthday is May 3")
    assert m.count() == 4


def test_forget_picks_a_clear_best_match(ts):
    system, *_ = ts
    store = system.services["memory"]
    store.add("My sister's name is Lea")
    store.add("I like green tea")
    store.add("My dentist is Dr. Weber")
    r = system.invoke("forget", {"query": "my sister's name"})
    assert r.ok and r.data["forgotten"] == ["My sister's name is Lea"]
    store.add("I like green tea in the morning")
    r = system.invoke("forget", {"query": "green tea"})
    assert not r.ok and "Several facts match" in r.text
    r = system.invoke("forget", {"query": "green tea", "all": True})
    assert r.ok and store.count() == 1


def test_memory_fast_paths(ts):
    system, *_ = ts
    a = Assistant(system.ctx, system, ScriptedLLM(answer("unused")))
    r = a.handle("What do you know about me?")
    assert (r.via, r.intent) == ("fast", "memory.list") and "haven't remembered anything" in r.text
    a.handle("remember that my sister's name is Lea")
    a.handle("Merk dir, dass ich Tee mag")
    r = a.handle("what do you know about me")
    assert r.text.startswith("You told me 2 things:") and "Lea" in r.text
    r = a.handle("forget that my sister's name is Lea")
    assert (r.intent, r.ok) == ("memory.forget", True) and "forgot" in r.text.lower()
    assert [f.text for f in system.services["memory"].all()] == ["Ich Tee mag"]
    system.ctx.config.set("general.language", "de")
    r = a.handle("Was weißt du über mich?")
    assert r.text.startswith("Du hast mir 1 Sache erzählt")


def test_profile_reaches_the_llm(ts):
    system, *_ = ts
    system.ctx.config.update({"profile.about": "Works as a nurse, night shifts", "profile.location": "Vienna",
                              "general.user_name": "Sam"})
    llm = ScriptedLLM(answer("Sure."))
    a = Assistant(system.ctx, system, llm)
    a.handle("tell me something nice")
    sys_msg = llm.seen[-1][0]["content"]
    assert "Works as a nurse, night shifts" in sys_msg and "Vienna" in sys_msg and "Call the user Sam." in sys_msg
