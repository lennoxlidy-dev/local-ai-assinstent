import io

from assistant.cli import DebugCLI
from assistant.core.context import bootstrap


def run(lines, home):
    ctx = bootstrap("cli", home=str(home), detect_hardware="off")
    out = io.StringIO()
    feed = iter(lines)
    cli = DebugCLI(ctx, out=out, read_line=lambda prompt: next(feed))
    assert cli.run() == 0
    return ctx, out.getvalue()


def test_cli_commands(home):
    ctx, out = run([":set voice.follow_up off", ":config voice.follow_up", ":set llm.max_agent_steps 999",
                    ":nope", "play some music", ":status", ":quit"], home)
    assert "voice.follow_up = false" in out
    assert "must be <= 40" in out
    assert "unknown command :nope" in out
    assert "jarvis> " in out and "[fast:media.play" in out   # free text -> assistant (fast path)
    assert "ram " in out
    assert ctx.config.data.voice.follow_up is False


def test_cli_eof_exits_cleanly(home):
    def raise_eof(prompt):
        raise EOFError

    ctx = bootstrap("cli", home=str(home), detect_hardware="off")
    assert DebugCLI(ctx, out=io.StringIO(), read_line=raise_eof).run() == 0
