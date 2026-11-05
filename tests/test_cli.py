from pathlib import Path

from paperquick import cli
from paperquick.core import Metadata


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


def test_confirmation_message_is_passed_as_an_argument(monkeypatch):
    captured = {}

    class Result:
        stdout = "yes\n"

    def fake_run(script, arguments):
        captured["script"] = script
        captured["arguments"] = arguments
        return Result()

    monkeypatch.setattr(cli, "_run_jxa", fake_run)
    metadata = Metadata(
        "An optimal control model for analyzing human postural balance",
        ["Kuo"],
        "1995",
        journal="IEEE Transactions on Biomedical Engineering",
        doi="10.1109/10.362914",
    )

    assert cli.confirm_candidate(Path("An Optimal Control.pdf"), metadata)
    assert len(captured["arguments"]) == 1
    assert "An Optimal Control.pdf?\n\nAn optimal control model" in captured[
        "arguments"
    ][0]
    assert "displayDialog(argv[0]" in captured["script"]
