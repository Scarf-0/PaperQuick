from pathlib import Path

from paperquick import cli


def test_jxa_receives_paths_as_arguments(monkeypatch):
    captured = {}

    class Result:
        stdout = "10.1000/example\n"

    def fake_run(script, arguments):
        captured["arguments"] = arguments
        return Result()

    monkeypatch.setattr(cli, "_run_jxa", fake_run)
    value = cli.prompt_for_query(Path("/tmp/a file %28test%29.pdf"), None)
    assert value == "10.1000/example"
    assert "a file %28test%29.pdf" in captured["arguments"][0]


def test_noninteractive_prompt_can_cancel(monkeypatch):
    monkeypatch.setenv("PAPERQUICK_NONINTERACTIVE", "1")
    assert cli.prompt_for_query(Path("paper.pdf"), None) is None
