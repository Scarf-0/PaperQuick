import plistlib
from pathlib import Path


def test_finder_service_keyboard_shortcuts():
    root = Path(__file__).parents[1] / "workflows"
    expected = {
        "PaperQuick - Rename PDF.workflow": "^@r",
        "PaperQuick - Copy PPT Citation.workflow": "^@c",
    }

    for workflow, shortcut in expected.items():
        with (root / workflow / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        assert info["NSServices"][0]["NSKeyEquivalent"]["default"] == shortcut
