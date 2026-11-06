from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from importlib.resources import files
from pathlib import Path
from typing import Callable, Iterable, Optional

from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
AUTO_CONFIDENCE = 0.90
AUTO_TITLE_SIMILARITY = 0.93
DOI_TITLE_SIMILARITY = 0.80
CACHE_VERSION = 2
GENERIC_TITLE_RE = re.compile(
    r"^(?:untitled|document|pdf|unknown year|unknown author|tbme\d*|pii\s*:|"
    r"doi\s*:|[a-z]{0,3}\.?\d[\w.%-]*)",
    re.IGNORECASE,
)
BOILERPLATE_RE = re.compile(
    r"^(?:journal of|vol\.?\s|volume\s|no\.?\s|printed in|www\.|https?://|"
    r"review article$|short communication$|abstract$|summary and conclusions$|"
    r"key\s*words?\s*:|received\s|accepted\s)",
    re.IGNORECASE,
)


@dataclass
class Metadata:
    title: str
    authors: list[str]
    year: str
    journal: str = ""
    short_journal: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    source: str = ""
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, value: dict) -> "Metadata":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value.get(key) for key in allowed})


@dataclass
class Evidence:
    titles: list[str]
    text: str
    years: set[str]
    volumes: set[str]
    pages: set[str]
    dois: list[str]


@dataclass
class RenameResult:
    path: Path
    status: str
    message: str
    new_path: Optional[Path] = None


def clean_text(value: object) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(value: str) -> str:
    value = clean_text(value).lower()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def extract_dois(value: str) -> list[str]:
    found: list[str] = []
    for match in DOI_RE.findall(value or ""):
        doi = match.rstrip(".,;:]}").strip()
        if doi.lower() not in {item.lower() for item in found}:
            found.append(doi)
    return found


def _article_dois(metadata: dict, page_text: str) -> list[str]:
    values = [
        value
        for key, value in metadata.items()
        if "ARTICLEDOI" in str(key).upper()
    ]
    values.append(page_text)
    values.extend(metadata.values())
    found: list[str] = []
    for value in values:
        for doi in extract_dois(clean_text(value)):
            if "(issn)" in doi.lower():
                continue
            if re.search(r"\.(?:fig|g|t|table)\d+$", doi, re.IGNORECASE):
                continue
            if doi.lower() == "10.1371/journal":
                continue
            if doi.lower() not in {item.lower() for item in found}:
                found.append(doi)
    return found


def _front_matter_text(page_text: str) -> str:
    return re.split(r"(?im)^\s*references\s*$", page_text, maxsplit=1)[0]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_useful_title(value: str) -> bool:
    value = clean_text(value)
    words = value.split()
    return (
        4 <= len(words) <= 30
        and len(value) >= 18
        and not GENERIC_TITLE_RE.match(value)
        and not BOILERPLATE_RE.match(value)
        and not DOI_RE.search(value)
    )


def _candidate_titles(metadata: dict, path: Path, page_text: str) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        value = clean_text(value)
        key = normalize_title(value)
        if _is_useful_title(value) and key not in {
            normalize_title(x) for x in candidates
        }:
            candidates.append(value)

    add(metadata.get("/Title", ""))

    filename = urllib.parse.unquote(path.stem)
    if not re.search(r"(?:^|\b)(?:jn\.|s?\d{4}-\d{3,4}|\d{4,}-\d+)", filename, re.I):
        add(filename)

    lines = [clean_text(line) for line in page_text.splitlines()[:35]]
    lines = [line for line in lines if line]
    # Keep page order and also consider the next line for wrapped titles.
    for index, line in enumerate(lines):
        if not BOILERPLATE_RE.match(line):
            add(line)
        if index + 1 >= len(lines):
            continue
        pair = lines[index : index + 2]
        joined = " ".join(pair)
        if any(BOILERPLATE_RE.match(item) for item in pair):
            continue
        if re.search(r"\b(?:department|university|institute|abstract)\b", joined, re.I):
            continue
        add(joined)

    return candidates[:8]


def read_pdf_evidence(path: Path) -> Evidence:
    reader = PdfReader(str(path))
    raw_metadata = dict(reader.metadata or {})
    page_text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])
    front_text = _front_matter_text(page_text)
    metadata_text = "\n".join(clean_text(value) for value in raw_metadata.values())
    combined = f"{metadata_text}\n{front_text}"
    titles = _candidate_titles(raw_metadata, path, front_text)
    years = set(YEAR_RE.findall(combined[:5000]))
    filename_text = urllib.parse.unquote(path.stem).replace("_", " ")
    years.update(YEAR_RE.findall(filename_text))

    volumes = set()
    pages = set()
    for match in re.finditer(r"(?:vol\.?|volume)\s*(\d{1,4})", combined, re.I):
        volumes.add(match.group(1))
    for match in re.finditer(
        r"\b\d{1,4}\s*:\s*(\d{1,5})(?:\s*[-–]\s*\d{1,5})?", combined
    ):
        pages.add(match.group(1))

    return Evidence(
        titles=titles,
        text=front_text,
        years=years,
        volumes=volumes,
        pages=pages,
        dois=_article_dois(raw_metadata, front_text),
    )


def _family_names(authors: Iterable[dict]) -> list[str]:
    result = []
    for author in authors or []:
        name = clean_text(author.get("family") or author.get("display_name") or "")
        if name:
            result.append(name)
    return result


def _first_date_year(value: dict) -> str:
    parts = (value or {}).get("date-parts") or []
    return str(parts[0][0]) if parts and parts[0] else ""


def metadata_from_crossref(item: dict, source: str = "Crossref") -> Metadata:
    title = clean_text((item.get("title") or [""])[0])
    journal = clean_text((item.get("container-title") or [""])[0])
    short_journal = clean_text((item.get("short-container-title") or [""])[0])
    return Metadata(
        title=title,
        authors=_family_names(item.get("author") or []),
        year=_first_date_year(
            item.get("published")
            or item.get("published-online")
            or item.get("published-print")
            or {}
        ),
        journal=journal,
        short_journal=short_journal,
        volume=clean_text(item.get("volume")),
        issue=clean_text(item.get("issue")),
        pages=clean_text(item.get("page")),
        doi=clean_text(item.get("DOI")),
        source=source,
    )


def metadata_from_openalex(item: dict) -> Metadata:
    location = item.get("primary_location") or {}
    source = location.get("source") or {}
    biblio = item.get("biblio") or {}
    first_page = clean_text(biblio.get("first_page"))
    last_page = clean_text(biblio.get("last_page"))
    pages = first_page
    if first_page and last_page and first_page != last_page:
        pages = f"{first_page}-{last_page}"
    doi = clean_text(item.get("doi")).removeprefix("https://doi.org/")
    authors = []
    for authorship in item.get("authorships") or []:
        display = clean_text((authorship.get("author") or {}).get("display_name"))
        if display:
            authors.append(display.split()[-1])
    return Metadata(
        title=clean_text(item.get("title")),
        authors=authors,
        year=clean_text(item.get("publication_year")),
        journal=clean_text(source.get("display_name")),
        volume=clean_text(biblio.get("volume")),
        issue=clean_text(biblio.get("issue")),
        pages=pages,
        doi=doi,
        source="OpenAlex",
    )


def metadata_is_complete(metadata: Optional[Metadata]) -> bool:
    title = clean_text(metadata.title) if metadata else ""
    return bool(
        metadata
        and len(title) >= 8
        and not GENERIC_TITLE_RE.match(title)
        and metadata.authors
        and re.fullmatch(r"(?:19|20)\d{2}", metadata.year or "")
    )


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()


def evidence_title_similarity(title: str, evidence: Evidence) -> float:
    similarity = max(
        (title_similarity(title, candidate) for candidate in evidence.titles),
        default=0.0,
    )
    normalized = normalize_title(title)
    if (
        len(normalized) >= 18
        and len(normalized.split()) >= 4
        and normalized in normalize_title(evidence.text)
    ):
        return 1.0
    return similarity


def score_candidate(candidate: Metadata, evidence: Evidence) -> float:
    if not candidate.title:
        return 0.0
    title_score = evidence_title_similarity(candidate.title, evidence)
    score = title_score * 0.85
    if candidate.year and candidate.year in evidence.years:
        score += 0.06
    if candidate.volume and candidate.volume in evidence.volumes:
        score += 0.04
    first_page = re.split(r"[-–]", candidate.pages or "")[0]
    if first_page and first_page in evidence.pages:
        score += 0.05
    return min(score, 1.0)


def _default_fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PaperQuick/0.1 (https://github.com/Scarf-0/PaperQuick)",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code < 600:
                raise
            if attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("Metadata request failed")


class Resolver:
    def __init__(self, fetch_json: Callable[[str], dict] = _default_fetch_json):
        self.fetch_json = fetch_json

    def _crossref_doi(self, doi: str) -> Optional[Metadata]:
        url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        try:
            item = self.fetch_json(url).get("message")
            if item:
                result = metadata_from_crossref(item)
                if not metadata_is_complete(result):
                    return None
                result.confidence = 1.0
                return result
        except Exception:
            return None
        return None

    def _crossref_title(self, title: str) -> list[Metadata]:
        fields = (
            "DOI,title,author,published,published-print,container-title,"
            "short-container-title,volume,issue,page"
        )
        url = (
            "https://api.crossref.org/works?rows=8&select="
            + fields
            + "&query.title="
            + urllib.parse.quote(title)
        )
        try:
            items = self.fetch_json(url).get("message", {}).get("items", [])
            return [metadata_from_crossref(item) for item in items]
        except Exception:
            return []

    def _openalex_title(self, title: str) -> list[Metadata]:
        url = "https://api.openalex.org/works?per-page=8&search=" + urllib.parse.quote(
            title
        )
        try:
            items = self.fetch_json(url).get("results", [])
            return [metadata_from_openalex(item) for item in items]
        except Exception:
            return []

    def resolve(self, evidence: Evidence, manual_query: str = "") -> Optional[Metadata]:
        query = clean_text(manual_query)
        manual_dois = extract_dois(query)
        for doi in manual_dois or evidence.dois:
            result = self._crossref_doi(doi)
            if result and (
                manual_dois
                or not (evidence.titles or clean_text(evidence.text))
                or evidence_title_similarity(result.title, evidence)
                >= DOI_TITLE_SIMILARITY
            ):
                return result

        titles = [query] if query and not manual_dois else evidence.titles
        candidates: dict[str, Metadata] = {}
        for title in titles[:4]:
            for candidate in self._crossref_title(title):
                key = candidate.doi.lower() or normalize_title(candidate.title)
                candidates[key] = candidate

            best = self._rank(
                candidates.values(), evidence, query if not manual_dois else ""
            )
            if (
                best
                and _is_useful_title(best.title)
                and best.confidence >= AUTO_CONFIDENCE
                and title_similarity(best.title, title) >= AUTO_TITLE_SIMILARITY
            ):
                return best

        best = self._rank(
            candidates.values(), evidence, query if not manual_dois else ""
        )
        if not best or best.confidence < AUTO_CONFIDENCE:
            for title in titles[:4]:
                for candidate in self._openalex_title(title):
                    key = candidate.doi.lower() or normalize_title(candidate.title)
                    candidates[key] = candidate

        return self._rank(
            candidates.values(), evidence, query if not manual_dois else ""
        )

    @staticmethod
    def _rank(
        candidates: Iterable[Metadata], evidence: Evidence, query: str = ""
    ) -> Optional[Metadata]:
        scoring_evidence = evidence
        if query:
            scoring_evidence = Evidence(
                [query],
                evidence.text,
                evidence.years,
                evidence.volumes,
                evidence.pages,
                [],
            )
        ranked = []
        for candidate in candidates:
            candidate.confidence = score_candidate(candidate, scoring_evidence)
            ranked.append(candidate)
        return max(ranked, key=lambda item: item.confidence, default=None)


def _load_abbreviations() -> dict[str, str]:
    path = files("paperquick").joinpath("data/journal_abbreviations.json")
    with path.open("r", encoding="utf-8") as stream:
        return {normalize_title(key): value for key, value in json.load(stream).items()}


JOURNAL_ABBREVIATIONS = _load_abbreviations()


def journal_label(metadata: Metadata) -> str:
    if metadata.short_journal and metadata.short_journal != metadata.journal:
        return metadata.short_journal
    return JOURNAL_ABBREVIATIONS.get(
        normalize_title(metadata.journal), metadata.journal
    )


def author_label(authors: list[str]) -> str:
    names = [clean_text(name) for name in authors if clean_text(name)]
    if not names:
        return "Unknown author"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return f"{names[0]} et al."


def sanitize_component(value: str, max_length: int = 130) -> str:
    value = clean_text(value)
    value = re.sub(r"[/\x00:]", "-", value)
    value = re.sub(r"[?*<>|\"]", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -")
    if len(value) <= max_length:
        return value
    shortened = value[:max_length].rsplit(" ", 1)[0]
    return shortened.strip(" .-") or value[:max_length]


def filename_for(metadata: Metadata) -> str:
    year = metadata.year or "Unknown year"
    title = sanitize_component(metadata.title).rstrip(".")
    return (
        f"{sanitize_component(year, 20)} - "
        f"{sanitize_component(author_label(metadata.authors), 80)} - "
        f"{title}.pdf"
    )


def citation_for(metadata: Metadata) -> str:
    parts = [author_label(metadata.authors)]
    journal = journal_label(metadata)
    if journal:
        parts.append(journal)
    if metadata.year:
        parts.append(metadata.year)
    locator = metadata.volume
    if metadata.pages:
        pages = re.sub(r"\s*[-–]\s*", "–", metadata.pages)
        locator = f"{locator}:{pages}" if locator else pages
    if locator:
        parts.append(locator)
    return ", ".join(parts) + "."


class Cache:
    def __init__(self, path: Optional[Path] = None):
        self.path = (
            path or Path.home() / "Library/Application Support/PaperQuick/cache.json"
        )
        self.data: dict[str, dict] = {}
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if stored.get("version") == CACHE_VERSION:
                self.data = stored.get("items", {})
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.data = {}

    def get(self, digest: str) -> Optional[Metadata]:
        value = self.data.get(digest)
        return Metadata.from_dict(value) if value else None

    def put(self, digest: str, metadata: Metadata) -> None:
        self.data[digest] = asdict(metadata)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix="cache-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"version": CACHE_VERSION, "items": self.data},
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class PaperQuick:
    def __init__(
        self,
        resolver: Optional[Resolver] = None,
        cache: Optional[Cache] = None,
        prompt: Optional[Callable[[Path, Optional[Metadata]], Optional[str]]] = None,
        confirm: Optional[Callable[[Path, Metadata], bool]] = None,
    ):
        self.resolver = resolver or Resolver()
        self.cache = cache or Cache()
        self.prompt = prompt
        self.confirm = confirm

    def metadata_for(self, path: Path) -> Optional[Metadata]:
        digest = file_sha256(path)
        cached = self.cache.get(digest)
        if metadata_is_complete(cached):
            return cached

        evidence = read_pdf_evidence(path)
        candidate = self.resolver.resolve(evidence)
        doi_match = bool(
            candidate
            and candidate.doi
            and any(candidate.doi.lower() == doi.lower() for doi in evidence.dois)
        )
        accepted = bool(
            candidate
            and metadata_is_complete(candidate)
            and (
                doi_match
                and (
                    not (evidence.titles or clean_text(evidence.text))
                    or evidence_title_similarity(candidate.title, evidence)
                    >= DOI_TITLE_SIMILARITY
                )
                or (
                    _is_useful_title(candidate.title)
                    and candidate.confidence >= AUTO_CONFIDENCE
                    and evidence_title_similarity(candidate.title, evidence)
                    >= AUTO_TITLE_SIMILARITY
                )
            )
        )
        if accepted:
            self.cache.put(digest, candidate)
            return candidate

        if not self.prompt:
            return None
        query = self.prompt(path, candidate)
        if not query:
            return None
        candidate = self.resolver.resolve(evidence, query)
        if not metadata_is_complete(candidate):
            return None
        if self.confirm and not self.confirm(path, candidate):
            return None
        self.cache.put(digest, candidate)
        return candidate

    def rename(self, paths: Iterable[Path]) -> list[RenameResult]:
        results: list[RenameResult] = []
        seen_hashes: dict[str, Path] = {}
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            try:
                if not path.is_file() or path.suffix.lower() != ".pdf":
                    results.append(RenameResult(path, "skipped", "Not a readable PDF"))
                    continue
                digest = file_sha256(path)
                if digest in seen_hashes:
                    results.append(
                        RenameResult(
                            path, "skipped", f"Duplicate of {seen_hashes[digest].name}"
                        )
                    )
                    continue
                seen_hashes[digest] = path
                metadata = self.metadata_for(path)
                if not metadata:
                    results.append(
                        RenameResult(path, "skipped", "Metadata not confirmed")
                    )
                    continue
                target = path.with_name(filename_for(metadata))
                if target == path:
                    results.append(
                        RenameResult(path, "skipped", "Already named correctly")
                    )
                    continue
                if target.exists():
                    message = "Target name already exists"
                    if file_sha256(target) == digest:
                        message = f"Duplicate of {target.name}"
                    results.append(RenameResult(path, "skipped", message))
                    continue
                path.rename(target)
                results.append(RenameResult(path, "renamed", "Renamed", target))
            except Exception as exc:
                results.append(RenameResult(path, "failed", clean_text(exc)))
        return results

    def citations(self, paths: Iterable[Path]) -> tuple[list[str], list[RenameResult]]:
        citations: list[str] = []
        results: list[RenameResult] = []
        seen: set[str] = set()
        for raw_path in paths:
            path = Path(raw_path).expanduser()
            try:
                if not path.is_file() or path.suffix.lower() != ".pdf":
                    results.append(RenameResult(path, "skipped", "Not a readable PDF"))
                    continue
                metadata = self.metadata_for(path)
                if not metadata:
                    results.append(
                        RenameResult(path, "skipped", "Metadata not confirmed")
                    )
                    continue
                key = metadata.doi.lower() or normalize_title(metadata.title)
                if key in seen:
                    results.append(RenameResult(path, "skipped", "Duplicate citation"))
                    continue
                seen.add(key)
                citations.append(citation_for(metadata))
                results.append(RenameResult(path, "cited", "Citation copied"))
            except Exception as exc:
                results.append(RenameResult(path, "failed", clean_text(exc)))
        return citations, results
