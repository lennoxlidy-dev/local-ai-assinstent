import io

from assistant.cli import CliConfirmer, DebugCLI


def test_cli_tool_commands_with_confirmation(ts):
    system, platform, conf, home = ts
    out = io.StringIO()
    feed = iter([
        ':tool volume {"level": 30}',
        ':tool create_file {"path": "hello.txt", "content": "hi"}', "n",   # deny
        ':tool create_file {"path": "hello.txt", "content": "hi"}', "y",   # allow once
        ":tool nope", ":tools volume", ":log 10", ":perm", ":quit",
    ])
    cli = DebugCLI(system.ctx, out=out, read_line=lambda prompt: next(feed), tools=system)
    system.gate.confirmer = CliConfirmer(cli)
    assert cli.run() == 0
    text = out.getvalue()
    assert "[ok] Volume 30%." in text
    assert "? Permission needed: Create file" in text and "[t] yes, for this task" in text
    assert "The user declined" in text
    assert (home / "hello.txt").read_text() == "hi"
    assert "Unknown tool 'nope'" in text
    assert "[system.volume]" in text
    assert "denied" in text and "approved" in text            # action log
    assert "files.delete       ask" in text                   # permissions table
