from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .core import Metadata, PaperQuick, clean_text

LOG_PATH = Path.home() / "Library/Logs/PaperQuick.log"


def _run_jxa(script: str, arguments: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script, *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def prompt_for_query(path: Path, candidate: Optional[Metadata]) -> Optional[str]:
    if os.environ.get("PAPERQUICK_NONINTERACTIVE") == "1":
        return None
    hint = ""
    if candidate:
        hint = f"\nBest match: {candidate.title} ({candidate.year})"
    message = (
        "PaperQuick could not identify this PDF confidently:\n"
        f"{path.name}{hint}\n\nEnter its DOI or full title:"
    )
    script = """
function run(argv) {
  var app = Application.currentApplication();
  app.includeStandardAdditions = true;
  try {
    var result = app.displayDialog(argv[0], {
      withTitle: 'PaperQuick', defaultAnswer: '', buttons: ['Cancel', 'Search'],
      defaultButton: 'Search', cancelButton: 'Cancel'
    });
    return result.textReturned;
  } catch (e) { return ''; }
}
"""
    result = _run_jxa(script, [message])
    return clean_text(result.stdout) or None


def confirm_candidate(path: Path, metadata: Metadata) -> bool:
    if os.environ.get("PAPERQUICK_NONINTERACTIVE") == "1":
        return False
    details = (
        f"{metadata.title}\n{', '.join(metadata.authors)} ({metadata.year})\n"
        f"{metadata.journal}\n{metadata.doi}"
    )
    script = """
function run(argv) {
  var app = Application.currentApplication();
  app.includeStandardAdditions = true;
  try {
    var message = 'Use this match for ' + argv[0] + '?\n\n' + argv[1];
    var result = app.displayDialog(message, {
      withTitle: 'PaperQuick', buttons: ['Cancel', 'Use Match'],
      defaultButton: 'Use Match', cancelButton: 'Cancel'
    });
    return result.buttonReturned === 'Use Match' ? 'yes' : 'no';
  } catch (e) { return 'no'; }
}
"""
    result = _run_jxa(script, [path.name, details])
    return clean_text(result.stdout) == "yes"


def notify(title: str, message: str) -> None:
    if os.environ.get("PAPERQUICK_FINDER") != "1":
        return
    script = """
function run(argv) {
  var app = Application.currentApplication();
  app.includeStandardAdditions = true;
  app.displayNotification(argv[1], {withTitle: argv[0]});
}
"""
    _run_jxa(script, [title, message])


def write_clipboard(value: str) -> None:
    subprocess.run(["/usr/bin/pbcopy"], input=value, text=True, check=True)


def _configure_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _summary(results) -> str:
    counts = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperquick")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("rename", "cite"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("pdfs", nargs="+", type=Path)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)
    service = PaperQuick(prompt=prompt_for_query, confirm=confirm_candidate)

    if args.command == "rename":
        results = service.rename(args.pdfs)
        for result in results:
            destination = f" -> {result.new_path}" if result.new_path else ""
            line = (
                f"{result.status.upper()}\t{result.path}{destination}\t{result.message}"
            )
            print(line)
            logging.info(line)
        summary = _summary(results)
        notify("PaperQuick Rename", summary)
        return 1 if any(result.status == "failed" for result in results) else 0

    citations, results = service.citations(args.pdfs)
    for result in results:
        logging.info("%s\t%s\t%s", result.status.upper(), result.path, result.message)
    if not citations:
        notify("PaperQuick Citation", _summary(results) or "No citation copied")
        return 1
    value = "\n".join(citations)
    write_clipboard(value)
    print(value)
    notify(
        "PaperQuick Citation",
        f"Copied {len(citations)} citation(s); {_summary(results)}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
