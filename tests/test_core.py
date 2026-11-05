import json
from pathlib import Path

import paperquick.core as core
from paperquick.core import (
    Cache,
    Evidence,
    Metadata,
    PaperQuick,
    Resolver,
    author_label,
    citation_for,
    extract_dois,
    filename_for,
    metadata_from_crossref,
    normalize_title,
    sanitize_component,
    score_candidate,
    _candidate_titles,
)


def test_extract_dois_cleans_trailing_punctuation():
    text = (
        "See https://doi.org/10.1152/jn.1998.80.3.1211. "
        "And 10.1016/S1050-6411(02)00085-8"
    )
    assert extract_dois(text) == [
        "10.1152/jn.1998.80.3.1211",
        "10.1016/S1050-6411(02)00085-8",
    ]


def test_author_filename_and_citation_formats():
    metadata = Metadata(
        title="Stiffness Control of Balance in Quiet Standing",
        authors=["Winter", "Patla", "Prince"],
        year="1998",
        journal="Journal of Neurophysiology",
        volume="80",
        pages="1211-1221",
    )
    assert author_label(metadata.authors) == "Winter et al."
    assert (
        filename_for(metadata)
        == "1998 - Winter et al. - Stiffness Control of Balance in Quiet Standing.pdf"
    )
    assert (
        citation_for(metadata) == "Winter et al., J Neurophysiol, 1998, 80:1211–1221."
    )


def test_two_author_label_and_sanitizing():
    assert author_label(["Horak", "Nashner"]) == "Horak & Nashner"
    assert (
        sanitize_component('A / title: with ? "bad" chars')
        == "A - title- with bad chars"
    )


def test_normalize_title_ignores_punctuation_and_case():
    assert normalize_title("A/P Balance: A Theory") == normalize_title(
        "a p balance - a theory"
    )


def test_title_candidates_keep_metadata_and_page_order():
    page = (
        "Review article\n"
        "Human balance and posture control during standing and walking\n"
        "D A Winter\n"
        "The fact that humans are bipeds and locomote over the ground"
    )
    titles = _candidate_titles(
        {"/Title": "PII: 0966-6362(96)82849-9"}, Path("coded.pdf"), page
    )
    assert titles[0] == "Human balance and posture control during standing and walking"


def test_score_uses_title_year_volume_and_page():
    evidence = Evidence(
        ["Stiffness Control of Balance in Quiet Standing"],
        "",
        {"1998"},
        {"80"},
        {"1211"},
        [],
    )
    exact = Metadata(
        title="Stiffness Control of Balance in Quiet Standing",
        authors=["Winter"],
        year="1998",
        volume="80",
        pages="1211-1221",
    )
    wrong = Metadata(
        title="Stiffness Control During Walking", authors=["Other"], year="2005"
    )
    assert score_candidate(exact, evidence) == 1.0
    assert score_candidate(wrong, evidence) < 0.7


def test_crossref_mapping_decodes_html():
    item = {
        "DOI": "10.1/example",
        "title": ["Human balance &amp; posture"],
        "author": [{"family": "Winter"}],
        "published": {"date-parts": [[1995, 12]]},
        "container-title": ["Gait &amp; Posture"],
        "volume": "3",
        "page": "193-214",
    }
    metadata = metadata_from_crossref(item)
    assert metadata.title == "Human balance & posture"
    assert metadata.journal == "Gait & Posture"
    assert metadata.year == "1995"


def test_resolver_exact_doi_and_title_fallback():
    calls = []

    def fetch(url):
        calls.append(url)
        if "/works/10.1152" in url:
            return {
                "message": {
                    "DOI": "10.1152/jn.1998.80.3.1211",
                    "title": ["Stiffness Control of Balance in Quiet Standing"],
                    "author": [{"family": "Winter"}],
                    "published": {"date-parts": [[1998]]},
                }
            }
        return {"message": {"items": []}}

    evidence = Evidence([], "", set(), set(), set(), ["10.1152/jn.1998.80.3.1211"])
    result = Resolver(fetch).resolve(evidence)
    assert result.doi == "10.1152/jn.1998.80.3.1211"
    assert result.confidence == 1.0
    assert len(calls) == 1


def test_cache_round_trip(tmp_path):
    cache = Cache(tmp_path / "cache.json")
    metadata = Metadata("Paper", ["Author"], "2024", doi="10.1/test")
    cache.put("digest", metadata)
    loaded = Cache(tmp_path / "cache.json").get("digest")
    assert loaded == metadata
    assert (
        "Paper" in json.loads((tmp_path / "cache.json").read_text())["digest"]["title"]
    )


def test_exact_doi_is_accepted_even_without_title_evidence(tmp_path, monkeypatch):
    pdf = tmp_path / "coded.pdf"
    pdf.write_bytes(b"placeholder")
    evidence = Evidence(
        ["Unhelpful extracted sentence"], "", set(), set(), set(), ["10.1/exact"]
    )
    monkeypatch.setattr(core, "read_pdf_evidence", lambda path: evidence)

    class ExactResolver:
        def resolve(self, evidence, manual_query=""):
            return Metadata(
                "Correct paper title",
                ["Author"],
                "2020",
                doi="10.1/exact",
                confidence=1.0,
            )

    service = PaperQuick(resolver=ExactResolver(), cache=Cache(tmp_path / "cache.json"))
    assert service.metadata_for(pdf).title == "Correct paper title"


def test_duplicate_selected_files_are_kept(tmp_path, monkeypatch):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"same")
    second.write_bytes(b"same")

    service = PaperQuick(cache=Cache(tmp_path / "cache.json"))
    metadata = Metadata("Known Paper", ["Author"], "2024")
    monkeypatch.setattr(service, "metadata_for", lambda path: metadata)

    results = service.rename([first, second])
    assert results[0].status == "renamed"
    assert results[1].status == "skipped"
    assert second.exists()
