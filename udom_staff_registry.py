from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UDOM_BASE_URL = "https://www.udom.ac.tz"
UDOM_STAFF_INDEX_URL = f"{UDOM_BASE_URL}/staff/index"
DEFAULT_TIMEOUT = (15, 45)

EMAIL_PATTERN = re.compile(
    r"[A-Z0-9._%+-]+@udom\.ac\.tz",
    re.IGNORECASE,
)

TITLE_WORDS = {
    "professor", "prof", "doctor", "dr", "mr", "mrs", "ms", "miss",
    "eng", "engineer", "rev", "reverend",
}


@dataclass(frozen=True)
class StaffIndexEntry:
    full_name: str
    profile_url: str
    availability_status: str = ""


def normalize_whitespace(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def normalize_institutional_email(value: str | None) -> str:
    return normalize_whitespace(value).lower()


def normalize_person_name(value: str | None) -> str:
    value = normalize_whitespace(value)
    if not value:
        return ""

    decomposed = unicodedata.normalize("NFD", value)
    value = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()

    tokens = [token for token in value.split() if token not in TITLE_WORDS]
    return " ".join(tokens)


def _initial_compatible(left: str, right: str) -> bool:
    return bool(left and right and left[0] == right[0])


def name_match_score(first: str | None, second: str | None) -> float:
    """Conservative score for matching a UDOM staff name to Ratiba instructor.

    Strong signals are the same surname plus compatible given-name/initial.
    The score is only one part of provisioning: verified institutional email
    and an official Ratiba instructor ID are also mandatory.
    """

    left = normalize_person_name(first)
    right = normalize_person_name(second)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return 0.0

    sequence_score = SequenceMatcher(None, left, right).ratio()
    sorted_score = SequenceMatcher(
        None,
        " ".join(sorted(left_tokens)),
        " ".join(sorted(right_tokens)),
    ).ratio()

    left_set = set(left_tokens)
    right_set = set(right_tokens)
    union = left_set | right_set
    token_score = len(left_set & right_set) / len(union) if union else 0.0

    same_last = left_tokens[-1] == right_tokens[-1]
    first_compatible = _initial_compatible(left_tokens[0], right_tokens[0])

    score = max(sequence_score, sorted_score, token_score)

    # Same surname + same/compatible first name is a very strong signal even
    # when one source omits middle names or uses initials.
    if same_last and first_compatible:
        score = max(score, 0.90)

    # Two exact shared tokens with the same surname is also strong.
    if same_last and len(left_set & right_set) >= 2:
        score = max(score, 0.94)

    # Do not accept fuzzy matches with different surnames unless the entire
    # normalized string is already extremely close.
    if not same_last and score < 0.97:
        score = min(score, 0.74)

    return round(score, 4)


def names_are_strong_match(first: str | None, second: str | None) -> bool:
    return name_match_score(first, second) >= 0.86


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "Ratiba-Staff-Registry/1.1"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Connection": "close",
        }
    )
    return session


def parse_total_staff_count(html: str) -> int | None:
    text = normalize_whitespace(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    match = re.search(
        r"Showing\s+\d+\s*-\s*\d+\s+of\s+(\d+)\s+items",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def parse_staff_index_page(html: str) -> list[StaffIndexEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[StaffIndexEntry] = []

    rows = soup.select("table tbody tr") or soup.select("table tr")
    for row in rows:
        link = row.select_one('a[href*="staff_profile"]')
        if link is None:
            continue

        href = normalize_whitespace(link.get("href"))
        if not href:
            continue

        cells = [
            normalize_whitespace(cell.get_text(" ", strip=True))
            for cell in row.find_all("td")
        ]
        if len(cells) < 4:
            continue

        # Public UDOM table currently uses:
        # # | First Name | Middle Name | Last Name | Availability | Actions
        if len(cells) >= 6:
            name_parts = cells[1:4]
            availability = cells[4]
        else:
            nonempty = [cell for cell in cells if cell]
            if len(nonempty) < 3:
                continue
            name_parts = nonempty[:3]
            availability = nonempty[3] if len(nonempty) > 3 else ""

        full_name = normalize_whitespace(" ".join(part for part in name_parts if part))
        if not full_name:
            continue

        entries.append(
            StaffIndexEntry(
                full_name=full_name,
                profile_url=urljoin(UDOM_BASE_URL, href),
                availability_status=availability,
            )
        )

    return entries


def fetch_staff_index_page(
    session: requests.Session,
    page: int,
) -> tuple[list[StaffIndexEntry], int | None]:
    response = session.get(
        UDOM_STAFF_INDEX_URL,
        params={"page": page},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    return parse_staff_index_page(response.text), parse_total_staff_count(response.text)


def crawl_staff_index(*, max_pages: int = 60) -> list[StaffIndexEntry]:
    """Read public staff-list pages without downloading every profile."""

    session = create_session()
    try:
        all_entries: list[StaffIndexEntry] = []
        seen_urls: set[str] = set()
        total_count: int | None = None

        for page in range(1, max_pages + 1):
            entries, reported_total = fetch_staff_index_page(session, page)
            if reported_total is not None:
                total_count = reported_total

            if not entries:
                break

            added = 0
            for entry in entries:
                if entry.profile_url in seen_urls:
                    continue
                seen_urls.add(entry.profile_url)
                all_entries.append(entry)
                added += 1

            if added == 0:
                break
            if total_count is not None and len(all_entries) >= total_count:
                break

        return all_entries
    finally:
        session.close()


def extract_profile_name(soup: BeautifulSoup) -> str:
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = normalize_whitespace(cells[0].get_text(" ", strip=True)).rstrip(":").lower()
        if label == "name":
            return normalize_whitespace(cells[1].get_text(" ", strip=True))

    # Public staff profile pages commonly render "Dr. Name" / "Prof. Name"
    # as a prominent heading.
    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = normalize_whitespace(heading.get_text(" ", strip=True))
        normalized = normalize_person_name(text)
        if not normalized:
            continue
        if normalized in {
            "personal biodata", "personal contacts", "office contacts",
            "contact us", "about",
        }:
            continue
        if len(normalized.split()) >= 2:
            return text

    return ""


def extract_staff_profile(html: str, profile_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    official_emails = sorted(
        {
            normalize_institutional_email(email)
            for email in EMAIL_PATTERN.findall(page_text)
            if normalize_institutional_email(email).endswith("@udom.ac.tz")
        }
    )

    return {
        "fullName": extract_profile_name(soup),
        "officialEmails": official_emails,
        "profileUrl": profile_url,
        "source": "UDOM_PUBLIC_STAFF_DIRECTORY",
    }


def fetch_staff_profile(
    session: requests.Session,
    entry: StaffIndexEntry,
) -> dict:
    response = session.get(entry.profile_url, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    record = extract_staff_profile(response.text, entry.profile_url)
    if not record.get("fullName"):
        record["fullName"] = entry.full_name
    record["availabilityStatus"] = entry.availability_status
    return record


def best_instructor_matches(
    staff_name: str,
    instructors: Iterable[dict],
) -> list[tuple[float, dict]]:
    scored: list[tuple[float, dict]] = []
    for instructor in instructors:
        name = instructor.get("instructorName") or instructor.get("name") or ""
        score = name_match_score(staff_name, name)
        if score > 0:
            scored.append((score, instructor))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def shortlist_entries_for_instructors(
    entries: Iterable[StaffIndexEntry],
    instructor_names: Iterable[str],
) -> list[StaffIndexEntry]:
    names = [name for name in instructor_names if normalize_person_name(name)]
    shortlisted: list[StaffIndexEntry] = []
    seen: set[str] = set()

    for entry in entries:
        if any(names_are_strong_match(entry.full_name, name) for name in names):
            if entry.profile_url not in seen:
                seen.add(entry.profile_url)
                shortlisted.append(entry)

    return shortlisted
