import json
import io
import urllib.error
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
    evidence_title_similarity,
    extract_dois,
    filename_for,
    metadata_is_complete,
    metadata_from_crossref,
    normalize_title,
    sanitize_component,
    score_candidate,
    _candidate_titles,
    _article_dois,
    _default_fetch_json,
    _front_matter_text,
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


def test_article_dois_prioritize_article_metadata_and_ignore_issn_doi():
    metadata = {
        "/WPS-JOURNALDOI": "10.1111/(ISSN)1600-0838",
        "/WPS-ARTICLEDOI": "10.1111/sms.14149",
    }
    assert _article_dois(metadata, "DOI: 10.1111/sms.14149") == [
        "10.1111/sms.14149"
    ]


def test_reference_section_is_excluded_from_front_matter():
    text = (
        "Actual paper title\nAuthor Name\nAbstract text\n"
        "References\nWrong cited paper. doi:10.1152/wrong.123"
    )
    front = _front_matter_text(text)
    assert "Actual paper title" in front
    assert "10.1152/wrong.123" not in front


def test_bibliography_metadata_label_does_not_hide_article_doi():
    text = "Article title\nBibliography\nDOI 10.1055/article.123\nAbstract"
    assert "10.1055/article.123" in _front_matter_text(text)


def test_evidence_title_similarity_finds_wrapped_title_in_front_text():
    evidence = Evidence(
        ["Publisher cover page"],
        "An analytical model for the ergometer rowing: inverse\n"
        "multibody dynamics analysis\nAuthor Name",
        set(),
        set(),
        set(),
        [],
    )
    assert (
        evidence_title_similarity(
            "An analytical model for the ergometer rowing: inverse multibody dynamics analysis",
            evidence,
        )
        == 1.0
    )


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


def test_crossref_mapping_prefers_first_publication_year():
    item = {
        "title": ["Online first paper title"],
        "author": [{"family": "Author"}],
        "published": {"date-parts": [[2022, 10, 27]]},
        "published-online": {"date-parts": [[2022, 10, 27]]},
        "published-print": {"date-parts": [[2025, 10, 3]]},
    }
    assert metadata_from_crossref(item).year == "2022"


def test_incomplete_journal_record_is_not_usable_metadata():
    journal = Metadata(
        title="Scandinavian Journal of Medicine & Science in Sports",
        authors=[],
        year="",
        doi="10.1111/(ISSN)1600-0838",
    )
    assert not metadata_is_complete(journal)


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


def test_resolver_skips_incomplete_journal_doi_for_article_doi():
    def fetch(url):
        if "%28ISSN%29" in url:
            return {
                "message": {
                    "DOI": "10.1111/(ISSN)1600-0838",
                    "title": ["Scandinavian Journal of Medicine & Science in Sports"],
                }
            }
        return {
            "message": {
                "DOI": "10.1111/sms.14149",
                "title": [
                    "Efficiency of cycling exercise: Quantification, mechanisms, and misunderstandings"
                ],
                "author": [
                    {"family": "MacDougall"},
                    {"family": "Falconer"},
                    {"family": "MacIntosh"},
                ],
                "published": {"date-parts": [[2022]]},
            }
        }

    evidence = Evidence(
        ["Efficiency of cycling exercise: Quantification, mechanisms, and misunderstandings"],
        "",
        {"2022"},
        {"32"},
        {"951"},
        ["10.1111/(ISSN)1600-0838", "10.1111/sms.14149"],
    )
    result = Resolver(fetch).resolve(evidence)
    assert result.doi == "10.1111/sms.14149"
    assert result.authors == ["MacDougall", "Falconer", "MacIntosh"]


def test_cache_round_trip(tmp_path):
    cache = Cache(tmp_path / "cache.json")
    metadata = Metadata("Paper", ["Author"], "2024", doi="10.1/test")
    cache.put("digest", metadata)
    loaded = Cache(tmp_path / "cache.json").get("digest")
    assert loaded == metadata
    assert (
        "Paper"
        in json.loads((tmp_path / "cache.json").read_text())["items"]["digest"][
            "title"
        ]
    )


def test_legacy_cache_is_invalidated(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps(
            {
                "digest": {
                    "title": "Old cached paper",
                    "authors": ["Author"],
                    "year": "2020",
                }
            }
        )
    )
    assert Cache(path).get("digest") is None


def test_short_title_occurrence_is_not_treated_as_full_title_evidence():
    evidence = Evidence(
        ["Sports Biomechanics ISSN print and online journal homepage"],
        "Sports Biomechanics ISSN print and online journal homepage",
        {"2022"},
        set(),
        set(),
        [],
    )
    assert evidence_title_similarity("Sports Biomechanics", evidence) < 0.93


def test_metadata_request_retries_rate_limit(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "rate limited", {}, None
            )
        return io.BytesIO(b'{"message": "ok"}')

    monkeypatch.setattr(core.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)
    assert _default_fetch_json("https://example.test") == {"message": "ok"}
    assert len(calls) == 2


def test_incomplete_cached_metadata_is_re_resolved(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"placeholder")
    cache = Cache(tmp_path / "cache.json")
    cache.put(
        core.file_sha256(pdf),
        Metadata("A Journal Name", [], "", doi="10.1/journal"),
    )
    evidence = Evidence(["A complete paper title"], "", {"2022"}, set(), set(), [])
    monkeypatch.setattr(core, "read_pdf_evidence", lambda path: evidence)

    class CompleteResolver:
        def resolve(self, evidence, manual_query=""):
            return Metadata(
                "A complete paper title",
                ["Author"],
                "2022",
                doi="10.1/article",
                confidence=1.0,
            )

    service = PaperQuick(resolver=CompleteResolver(), cache=cache)
    assert service.metadata_for(pdf).doi == "10.1/article"


def test_exact_doi_is_accepted_even_without_title_evidence(tmp_path, monkeypatch):
    pdf = tmp_path / "coded.pdf"
    pdf.write_bytes(b"placeholder")
    evidence = Evidence(
        [], "", set(), set(), set(), ["10.1/exact"]
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


def test_exact_doi_with_conflicting_title_is_not_accepted(tmp_path, monkeypatch):
    pdf = tmp_path / "coded.pdf"
    pdf.write_bytes(b"placeholder")
    evidence = Evidence(
        ["Coupling athlete force profiles to rowing oar and boat loads"],
        "Coupling athlete force profiles to rowing oar and boat loads",
        {"2025"},
        set(),
        set(),
        ["10.1/reference"],
    )
    monkeypatch.setattr(core, "read_pdf_evidence", lambda path: evidence)

    class WrongResolver:
        def resolve(self, evidence, manual_query=""):
            return Metadata(
                "The effect of muscle-tendon analyses during cycling",
                ["Other"],
                "2018",
                doi="10.1/reference",
                confidence=1.0,
            )

    service = PaperQuick(resolver=WrongResolver(), cache=Cache(tmp_path / "cache.json"))
    assert service.metadata_for(pdf) is None


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
