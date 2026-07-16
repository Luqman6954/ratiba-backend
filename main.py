import base64
import hashlib
import json
import os
import re
import time
from threading import Lock
from time import monotonic
from typing import Any

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import firebase_admin
from firebase_admin import credentials, firestore

try:
    from academic_units import (
        AcademicUnitService,
        AcademicUnitServiceError,
    )
except ImportError:
    AcademicUnitService = None

    class AcademicUnitServiceError(Exception):
        pass


app = FastAPI(
    title="UDOM Ratiba API",
    description="Fetches and converts UDOM timetable information into JSON",
    version="2.7.0",
)

BASE_URL = "https://ratiba.udom.ac.tz"

WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

SESSION_START_PATTERN = re.compile(
    rf"""
    (?P<start>\d{{1,2}}:\d{{2}})
    \s*-\s*
    (?P<end>\d{{1,2}}:\d{{2}})
    \s*,\s*
    (?P<day>{'|'.join(WEEKDAYS)})
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_whitespace(value: str) -> str:
    if value is None:
        return ""

    return " ".join(value.split()).strip()



def extract_study_year(
    programme_code: str,
):
    """
    Extract the year of study from the final number in a UDOM
    programme code.

    Examples:
        IS2   -> 2
        BIS3  -> 3
        SE4   -> 4
        ITBA1 -> 1

    Returns None when the programme code has no trailing year.
    """

    normalized_code = normalize_whitespace(
        programme_code
    )

    match = re.search(
        r"(\d+)$",
        normalized_code,
    )

    if match is None:
        return None

    try:
        study_year = int(
            match.group(1)
        )
    except ValueError:
        return None

    return study_year if study_year > 0 else None

def get_csrf_token(soup: BeautifulSoup) -> str:
    meta_tag = soup.find(
        "meta",
        attrs={"name": "csrf-token"},
    )

    if meta_tag and meta_tag.get("content"):
        return meta_tag.get("content")

    hidden_input = soup.find(
        "input",
        attrs={"name": "_csrf-backend"},
    )

    if hidden_input and hidden_input.get("value"):
        return hidden_input.get("value")

    raise RuntimeError(
        "CSRF token was not found."
    )


def create_udom_session() -> requests.Session:
    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "Ratiba-API/2.1"
            ),
            "Connection": "close",
        }
    )

    return session


def open_ratiba_index(
    session: requests.Session,
):
    index_url = f"{BASE_URL}/downloads/index"

    response = session.get(
        index_url,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    return index_url, soup


def download_academic_years():
    session = create_udom_session()

    try:
        _, soup = open_ratiba_index(
            session
        )

        year_select = (
            soup.select_one("select#year")
            or soup.select_one(
                'select[name="year"]'
            )
        )

        if year_select is None:
            raise RuntimeError(
                "Academic-year list was not found."
            )

        academic_years = []

        for option in year_select.find_all(
            "option"
        ):
            year_id = option.get(
                "value",
                "",
            ).strip()

            year_name = option.get_text(
                " ",
                strip=True,
            )

            if not year_id:
                continue

            academic_years.append(
                {
                    "academicYearId": year_id,
                    "academicYear": year_name,
                }
            )

        return academic_years

    finally:
        session.close()


def download_semesters(
    year_id: str,
):
    session = create_udom_session()

    try:
        index_url, index_soup = (
            open_ratiba_index(
                session
            )
        )

        csrf_token = get_csrf_token(
            index_soup
        )

        params = {
            "_csrf-backend": csrf_token,
            "year": year_id,
            "semester": "",
            "type": "",
            "option": "",
            "data": "",
        }

        headers = {
            "X-CSRF-Token": csrf_token,
            "X-Requested-With": (
                "XMLHttpRequest"
            ),
            "Referer": index_url,
        }

        response = session.get(
            (
                f"{BASE_URL}"
                "/downloads/fetch-semesters"
            ),
            params=params,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        semesters = []

        for option in soup.find_all(
            "option"
        ):
            semester_id = option.get(
                "value",
                "",
            ).strip()

            semester_name = option.get_text(
                " ",
                strip=True,
            )

            if not semester_id:
                continue

            semesters.append(
                {
                    "semesterId": semester_id,
                    "semester": semester_name,
                }
            )

        if not semesters:
            raise RuntimeError(
                "No semesters were found for "
                f"academic year ID {year_id}."
            )

        return semesters

    finally:
        session.close()


def download_programmes():
    session = create_udom_session()

    try:
        index_url, index_soup = (
            open_ratiba_index(
                session
            )
        )

        csrf_token = get_csrf_token(
            index_soup
        )

        params = {
            "_csrf-backend": csrf_token,
            "year": "12",
            "semester": "3368",
            "type": "1",
            "option": "programme",
            "data": "",
        }

        headers = {
            "X-CSRF-Token": csrf_token,
            "X-Requested-With": (
                "XMLHttpRequest"
            ),
            "Referer": index_url,
        }

        response = session.get(
            f"{BASE_URL}/downloads/data",
            params=params,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        programme_select = (
            soup.select_one("select#data")
        )

        if programme_select is None:
            raise RuntimeError(
                "Programme list was not found."
            )

        programmes = []

        for option in (
            programme_select.find_all(
                "option"
            )
        ):
            programme_id = option.get(
                "value",
                "",
            ).strip()

            label = option.get_text(
                " ",
                strip=True,
            )

            if not programme_id:
                continue

            if " - " in label:
                (
                    programme_code,
                    programme_name,
                ) = label.split(
                    " - ",
                    1,
                )
            else:
                programme_code = ""
                programme_name = label

            programmes.append(
                {
                    "programmeId": (
                        programme_id
                    ),
                    "programmeCode": (
                        programme_code.strip()
                    ),
                    "programmeName": (
                        programme_name.strip()
                    ),
                    "studyYear": (
                        extract_study_year(
                            programme_code
                        )
                    ),
                }
            )

        return programmes

    finally:
        session.close()


def download_timetable_html(
    programme_id: str,
):
    session = create_udom_session()

    try:
        index_url, index_soup = (
            open_ratiba_index(
                session
            )
        )

        csrf_token = get_csrf_token(
            index_soup
        )

        params = {
            "_csrf-backend": csrf_token,
            "year": "12",
            "semester": "3368",
            "type": "1",
            "option": "programme",
            "data": programme_id,
        }

        headers = {
            "X-CSRF-Token": csrf_token,
            "X-Requested-With": (
                "XMLHttpRequest"
            ),
            "Referer": index_url,
        }

        response = session.get(
            f"{BASE_URL}/downloads/view",
            params=params,
            headers=headers,
            timeout=30,
        )

        response.raise_for_status()

        return response.text

    finally:
        session.close()


def extract_course_names(
    soup: BeautifulSoup,
):
    course_names = {}

    description_span = soup.find(
        "span",
        string=lambda text: (
            text
            and "DESCRIPTION"
            in text.upper()
        ),
    )

    if description_span is None:
        return course_names

    description_table = (
        description_span.find_next(
            "table"
        )
    )

    if description_table is None:
        return course_names

    for row in description_table.find_all(
        "tr"
    ):
        columns = row.find_all("td")

        if len(columns) < 3:
            continue

        course_code = normalize_whitespace(
            columns[1].get_text(
                " ",
                strip=True,
            )
        )

        course_name = normalize_whitespace(
            columns[2].get_text(
                " ",
                strip=True,
            )
        ).lstrip("-").strip()

        if course_code:
            course_names[
                course_code
            ] = course_name

    return course_names


def split_course_and_group(
    course_part: str,
    course_names: dict,
):
    normalized_course_part = (
        normalize_whitespace(
            course_part
        )
    )

    sorted_codes = sorted(
        course_names.keys(),
        key=len,
        reverse=True,
    )

    for known_code in sorted_codes:
        if normalized_course_part.startswith(
            known_code
        ):
            group = (
                normalized_course_part[
                    len(known_code):
                ].strip()
            )

            return known_code, group

    return normalized_course_part, ""


FIELD_LABEL_PATTERN = re.compile(
    r"(?P<label>Staff|Students|Venue)\s*:\s*",
    re.IGNORECASE,
)


def clean_field_value(
    value: str,
) -> str:
    """
    Normalize one field without allowing delimiters from
    neighbouring fields to become part of the value.
    """

    normalized = normalize_whitespace(
        value
    )

    return normalized.strip(
        " ;"
    )


def extract_labeled_fields(
    record_text: str,
):
    """
    Extract every labelled field by using the next known field
    label as the hard boundary.

    Example:
        Staff: A ; Students: B ; Venue: C

    Staff can only end where Students/Venue starts.
    Students can only end where Venue/Staff starts.
    Venue can only end where another known field starts or
    where this single record ends.
    """

    matches = list(
        FIELD_LABEL_PATTERN.finditer(
            record_text
        )
    )

    fields = {
        "staff": [],
        "students": [],
        "venue": [],
    }

    for index, match in enumerate(
        matches
    ):
        label = (
            match.group("label")
            .lower()
            .strip()
        )

        value_start = match.end()

        if index + 1 < len(matches):
            value_end = matches[
                index + 1
            ].start()
        else:
            value_end = len(record_text)

        value = clean_field_value(
            record_text[
                value_start:value_end
            ]
        )

        if value:
            fields[label].append(
                value
            )

    return fields


def has_exact_field_structure(
    record_text: str,
) -> bool:
    """
    A valid UDOM session record must have exactly one Staff,
    one Students and one Venue field.

    If a record accidentally contains two Staff/Students/Venue
    blocks, it means another session leaked into this record.
    Reject it instead of returning corrupted JSON.
    """

    counts = {
        "staff": 0,
        "students": 0,
        "venue": 0,
    }

    for match in FIELD_LABEL_PATTERN.finditer(
        record_text
    ):
        label = (
            match.group("label")
            .lower()
            .strip()
        )

        counts[label] += 1

    return all(
        count == 1
        for count in counts.values()
    )


def first_field_value(
    fields: dict,
    field_name: str,
) -> str:
    values = fields.get(
        field_name,
        [],
    )

    if not values:
        return ""

    return values[0]


def build_session_record_pattern(
    course_names: dict,
):
    """
    Build record boundaries from the course codes in UDOM's
    DESCRIPTION table.

    This is important because one timetable cell may contain
    several sessions with the same time/day. Time alone is not
    therefore a safe record boundary.
    """

    known_codes = [
        normalize_whitespace(code)
        for code in course_names.keys()
        if normalize_whitespace(code)
    ]

    known_codes.sort(
        key=len,
        reverse=True,
    )

    if known_codes:
        course_code_pattern = "|".join(
            re.escape(code)
            for code in known_codes
        )

        course_part_pattern = (
            rf"(?:{course_code_pattern})"
            r"(?:\s+[A-Za-z0-9][A-Za-z0-9()./_-]*)*"
        )

    else:
        # Safe fallback for a timetable whose DESCRIPTION
        # table is temporarily unavailable.
        course_part_pattern = (
            r"[A-Z][A-Z0-9./&-]*"
            r"(?:\s+[A-Z0-9./&-]+){0,3}"
            r"\s+\d{2,4}[A-Z]?"
            r"(?:\s+[A-Za-z0-9][A-Za-z0-9()./_-]*)*"
        )

    return re.compile(
        rf"""
        (?<!\w)
        (?P<course>{course_part_pattern})
        \s*-\s*
        (?P<session_type>[^;]+?)
        \s*;\s*
        Staff\s*:
        """,
        re.IGNORECASE | re.VERBOSE,
    )


def build_parsed_session(
    *,
    course_part: str,
    session_type: str,
    record_text: str,
    start_time: str,
    end_time: str,
    day: str,
    course_names: dict,
):
    """
    Build one session only after its field structure has been
    proven to be isolated from neighbouring sessions.
    """

    if not has_exact_field_structure(
        record_text
    ):
        return None

    fields = extract_labeled_fields(
        record_text
    )

    lecturer_name = first_field_value(
        fields,
        "staff",
    )

    students_text = first_field_value(
        fields,
        "students",
    )

    venue = first_field_value(
        fields,
        "venue",
    )

    # A field containing another time/day signature means the
    # parser crossed a session boundary. Fail closed.
    protected_values = (
        lecturer_name,
        students_text,
        venue,
        normalize_whitespace(
            session_type
        ),
    )

    if any(
        SESSION_START_PATTERN.search(value)
        for value in protected_values
        if value
    ):
        return None

    course_code, group = (
        split_course_and_group(
            course_part,
            course_names,
        )
    )

    student_groups = [
        normalize_whitespace(student)
        for student in students_text.split(",")
        if normalize_whitespace(student)
    ]

    return {
        "day": day,
        "startTime": start_time,
        "endTime": end_time,
        "courseCode": course_code,
        "courseName": course_names.get(
            course_code,
            "",
        ),
        "group": group,
        "sessionType": (
            normalize_whitespace(
                session_type
            )
        ),
        "lecturerName": lecturer_name,
        "studentGroups": student_groups,
        "venue": venue,
    }


def parse_single_unambiguous_record(
    session_body: str,
    *,
    start_time: str,
    end_time: str,
    day: str,
    course_names: dict,
):
    """
    Fallback parser used only when the cell contains exactly one
    Staff/Students/Venue block.

    Ambiguous multi-record text is deliberately rejected so no
    field can swallow data from another session.
    """

    if not has_exact_field_structure(
        session_body
    ):
        return None

    staff_match = re.search(
        r"\s*;\s*Staff\s*:",
        session_body,
        re.IGNORECASE,
    )

    if staff_match is None:
        return None

    course_session_text = (
        normalize_whitespace(
            session_body[
                :staff_match.start()
            ]
        )
    )

    if " - " not in course_session_text:
        return None

    (
        course_part,
        session_type,
    ) = course_session_text.rsplit(
        " - ",
        1,
    )

    return build_parsed_session(
        course_part=course_part,
        session_type=session_type,
        record_text=session_body,
        start_time=start_time,
        end_time=end_time,
        day=day,
        course_names=course_names,
    )


def parse_session_chunk(
    session_text: str,
    session_start_match,
    course_names: dict,
):
    """
    Parse one time/day block.

    A time/day block may contain multiple course records. Each
    course record is isolated before Staff, Students or Venue is
    extracted.
    """

    start_time = (
        session_start_match
        .group("start")
        .strip()
    )

    end_time = (
        session_start_match
        .group("end")
        .strip()
    )

    day = (
        session_start_match
        .group("day")
        .title()
        .strip()
    )

    session_body = normalize_whitespace(
        session_text[
            session_start_match.end():
        ]
    )

    if not session_body:
        return []

    record_pattern = (
        build_session_record_pattern(
            course_names
        )
    )

    record_matches = list(
        record_pattern.finditer(
            session_body
        )
    )

    parsed_sessions = []

    if not record_matches:
        fallback_session = (
            parse_single_unambiguous_record(
                session_body,
                start_time=start_time,
                end_time=end_time,
                day=day,
                course_names=course_names,
            )
        )

        if fallback_session is not None:
            parsed_sessions.append(
                fallback_session
            )

        return parsed_sessions

    for index, record_match in enumerate(
        record_matches
    ):
        record_start = record_match.start()

        if index + 1 < len(record_matches):
            record_end = record_matches[
                index + 1
            ].start()
        else:
            record_end = len(
                session_body
            )

        record_text = session_body[
            record_start:record_end
        ]

        parsed_session = build_parsed_session(
            course_part=record_match.group(
                "course"
            ),
            session_type=record_match.group(
                "session_type"
            ),
            record_text=record_text,
            start_time=start_time,
            end_time=end_time,
            day=day,
            course_names=course_names,
        )

        if parsed_session is not None:
            parsed_sessions.append(
                parsed_session
            )

    return parsed_sessions


def parse_sessions_from_cell(
    cell,
    course_names: dict,
):
    full_text = normalize_whitespace(
        cell.get_text(
            " ",
            strip=True,
        )
    )

    if (
        "Staff" not in full_text
        or "Students" not in full_text
        or "Venue" not in full_text
    ):
        return []

    start_matches = list(
        SESSION_START_PATTERN.finditer(
            full_text
        )
    )

    if not start_matches:
        return []

    parsed_sessions = []

    for index, start_match in enumerate(
        start_matches
    ):
        chunk_start = start_match.start()

        if index + 1 < len(start_matches):
            chunk_end = (
                start_matches[index + 1]
                .start()
            )
        else:
            chunk_end = len(full_text)

        session_chunk = full_text[
            chunk_start:chunk_end
        ]

        local_start_match = (
            SESSION_START_PATTERN.search(
                session_chunk
            )
        )

        if local_start_match is None:
            continue

        parsed_sessions.extend(
            parse_session_chunk(
                session_chunk,
                local_start_match,
                course_names,
            )
        )

    return parsed_sessions


def parse_timetable(
    html: str,
    programme_id: str,
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    heading = soup.find("h4")

    if heading is None:
        raise RuntimeError(
            "Timetable heading was not found."
        )

    heading_text = heading.get_text(
        " ",
        strip=True,
    )

    heading_match = re.search(
        (
            r"(.+?)\s*-\s*"
            r"(.+?)\s+Timetable"
        ),
        heading_text,
        re.IGNORECASE,
    )

    programme_code = ""
    category = ""

    if heading_match:
        programme_code = (
            heading_match.group(1)
            .strip()
        )

        category = (
            heading_match.group(2)
            .title()
            .strip()
        )

    course_names = extract_course_names(
        soup
    )

    timetable_table = soup.select_one(
        "table.responsive-sm"
    )

    if timetable_table is None:
        raise RuntimeError(
            "Main timetable table was not found."
        )

    sessions = []
    seen_session_keys = set()

    for row in timetable_table.select(
        "tbody > tr"
    ):
        cells = row.find_all(
            "td",
            recursive=False,
        )

        if not cells:
            continue

        row_day = normalize_whitespace(
            cells[0].get_text(
                " ",
                strip=True,
            )
        )

        if row_day not in WEEKDAYS:
            continue

        for cell in cells[1:]:
            cell_sessions = (
                parse_sessions_from_cell(
                    cell,
                    course_names,
                )
            )

            for parsed_session in cell_sessions:
                session_day = (
                    parsed_session.get("day")
                    or row_day
                )

                session = {
                    "programmeCode": programme_code,
                    "category": category,
                    "day": session_day,
                    "startTime": parsed_session["startTime"],
                    "endTime": parsed_session["endTime"],
                    "courseCode": parsed_session["courseCode"],
                    "courseName": parsed_session["courseName"],
                    "group": parsed_session["group"],
                    "sessionType": parsed_session["sessionType"],
                    "lecturerName": parsed_session["lecturerName"],
                    "studentGroups": parsed_session["studentGroups"],
                    "venue": parsed_session["venue"],
                }

                session_key = (
                    session["day"],
                    session["startTime"],
                    session["endTime"],
                    session["courseCode"],
                    session["group"],
                    session["sessionType"],
                    session["lecturerName"],
                    tuple(session["studentGroups"]),
                    session["venue"],
                )

                if session_key in seen_session_keys:
                    continue

                seen_session_keys.add(
                    session_key
                )

                sessions.append(
                    session
                )

    return {
        "programmeId": programme_id,
        "programmeCode": programme_code,
        "category": category,
        "sessionCount": len(sessions),
        "source": "UDOM Ratiba",
        "sessions": sessions,
    }


# =====================================================================
# RESTORED COMPLETE BACKEND FEATURES
# =====================================================================

DEFAULT_TIMEOUT = (20, 120)
TIMETABLE_TIMEOUT = (20, 180)
REFERENCE_CACHE_TTL_SECONDS = 30 * 60

_reference_cache = {}
_reference_cache_lock = Lock()

academic_unit_service = (
    AcademicUnitService()
    if AcademicUnitService is not None
    else None
)


# ---------------------------------------------------------------------
# FIREBASE
# ---------------------------------------------------------------------

def initialize_firestore():
    """
    Initialize Firestore when the Vercel/local environment contains
    firebase_service_account_b64. Public UDOM endpoints remain usable
    even when Firebase is not configured locally.
    """

    try:
        firebase_app = firebase_admin.get_app()

    except ValueError:
        encoded_credentials = os.getenv(
            "firebase_service_account_b64"
        )

        if not encoded_credentials:
            return None

        try:
            decoded_credentials = base64.b64decode(
                encoded_credentials
            ).decode("utf-8")

            service_account_info = json.loads(
                decoded_credentials
            )

        except Exception as error:
            raise RuntimeError(
                "Unable to decode Firebase service-account credentials"
            ) from error

        firebase_credential = credentials.Certificate(
            service_account_info
        )

        firebase_app = firebase_admin.initialize_app(
            firebase_credential
        )

    return firestore.client(
        app=firebase_app
    )


firestore_db = initialize_firestore()


class PublishTimetableRequest(BaseModel):
    academicYearId: str
    academicYear: str

    semesterId: str
    semester: str

    programmeId: str
    programmeCode: str
    programmeName: str

    categoryId: str = "1"


def clean_document_id_part(value: str) -> str:
    value = normalize_whitespace(value)

    return re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        value,
    ).strip("-")


def build_publication_id(
        request: PublishTimetableRequest,
) -> str:
    return "_".join(
        [
            clean_document_id_part(
                request.academicYearId
            ),
            clean_document_id_part(
                request.semesterId
            ),
            clean_document_id_part(
                request.categoryId
            ),
            clean_document_id_part(
                request.programmeId
            ),
        ]
    )


def clean_display_text(value: Any) -> str:
    return normalize_whitespace(value)


def normalize_session_value(value: Any) -> str:
    return normalize_whitespace(value).upper()


def clean_student_groups(values) -> list[str]:
    cleaned_groups = []
    seen_groups = set()

    for value in values or []:
        cleaned_value = clean_display_text(value)

        if not cleaned_value:
            continue

        comparison_value = cleaned_value.upper()

        if comparison_value in seen_groups:
            continue

        seen_groups.add(comparison_value)
        cleaned_groups.append(cleaned_value)

    return sorted(
        cleaned_groups,
        key=str.upper,
    )


def build_official_session_id(
        request: PublishTimetableRequest,
        session: dict,
) -> str:
    """
    Build a stable ID. Programme identity is deliberately omitted so
    shared sessions imported through several programmes remain one
    Firestore session.
    """

    normalized_groups = sorted(
        {
            normalize_session_value(group)
            for group in (
                session.get("studentGroups")
                or []
            )
            if normalize_session_value(group)
        }
    )

    identity = {
        "academicYearId": normalize_session_value(
            request.academicYearId
        ),
        "semesterId": normalize_session_value(
            request.semesterId
        ),
        "categoryId": normalize_session_value(
            request.categoryId
        ),
        "courseCode": normalize_session_value(
            session.get("courseCode")
        ),
        "day": normalize_session_value(
            session.get("day")
        ),
        "startTime": normalize_session_value(
            session.get("startTime")
        ),
        "endTime": normalize_session_value(
            session.get("endTime")
        ),
        "venue": normalize_session_value(
            session.get("venue")
        ),
        "lecturerName": normalize_session_value(
            session.get("lecturerName")
        ),
        "sessionType": normalize_session_value(
            session.get("sessionType")
        ),
        "studentGroups": normalized_groups,
    }

    serialized = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    )

    digest = hashlib.sha256(
        serialized.encode("utf-8")
    ).hexdigest()

    return f"udom_{digest[:32]}"


def build_firestore_session_document(
        request: PublishTimetableRequest,
        session: dict,
        session_id: str,
) -> dict:
    student_groups = clean_student_groups(
        session.get("studentGroups")
    )

    requested_programme_code = clean_display_text(
        request.programmeCode
    )

    existing_groups = {
        group.upper()
        for group in student_groups
    }

    if (
            requested_programme_code
            and requested_programme_code.upper()
            not in existing_groups
    ):
        student_groups.append(
            requested_programme_code
        )
        student_groups.sort(
            key=str.upper
        )

    study_year = extract_study_year(
        request.programmeCode
    )

    year_label = (
        f"Year {study_year}"
        if study_year is not None
        else ""
    )

    publication_id = build_publication_id(
        request
    )

    return {
        "firebaseId": session_id,
        "officialSessionId": session_id,
        "publicationId": publication_id,

        "academicYearId": request.academicYearId,
        "academicYear": request.academicYear,
        "semesterId": request.semesterId,
        "semester": request.semester,
        "categoryId": request.categoryId,
        "category": (
            clean_display_text(
                session.get("category")
            )
            or "Teaching"
        ),

        "programmeId": request.programmeId,
        "programmeCode": request.programmeCode,
        "programmeName": request.programmeName,

        "programmeIds": [
            request.programmeId
        ],
        "programmeCodes": [
            request.programmeCode
        ],
        "programmeNames": [
            request.programmeName
        ],
        "studentGroups": student_groups,

        # Existing Android compatibility fields.
        "department": "",
        "course": request.programmeName,
        "year": year_label,

        "courseCode": clean_display_text(
            session.get("courseCode")
        ),
        "courseName": clean_display_text(
            session.get("courseName")
        ),
        "lecturerName": clean_display_text(
            session.get("lecturerName")
        ),
        "day": clean_display_text(
            session.get("day")
        ),
        "startTime": clean_display_text(
            session.get("startTime")
        ),
        "endTime": clean_display_text(
            session.get("endTime")
        ),
        "venue": clean_display_text(
            session.get("venue")
        ),
        "sessionType": clean_display_text(
            session.get("sessionType")
        ),
        "group": clean_display_text(
            session.get("group")
        ),

        "sessionStatus": "Pending",
        "source": "UDOM_RATIBA",
        "official": True,
        "active": True,

        "oneHourReminderSent": False,
        "lecturerReminderSent": False,
        "crReminderSent": False,
        "crFollowUpReminderSent": False,

        "updatedAt": firestore.SERVER_TIMESTAMP,
    }


def write_official_sessions_to_firestore(
        request: PublishTimetableRequest,
        sessions: list[dict],
) -> dict:
    if firestore_db is None:
        raise RuntimeError(
            "Firebase is not configured"
        )

    if not sessions:
        raise ValueError(
            "Cannot publish an empty timetable"
        )

    publication_id = build_publication_id(
        request
    )

    unique_documents = {}

    for session in sessions:
        session_id = build_official_session_id(
            request,
            session,
        )

        document = build_firestore_session_document(
            request,
            session,
            session_id,
        )

        document["programmeIds"] = (
            firestore.ArrayUnion(
                [request.programmeId]
            )
        )

        document["programmeCodes"] = (
            firestore.ArrayUnion(
                [request.programmeCode]
            )
        )

        document["programmeNames"] = (
            firestore.ArrayUnion(
                [request.programmeName]
            )
        )

        unique_documents[
            session_id
        ] = document

    batch = firestore_db.batch()
    session_ids = []

    for session_id, document in (
            unique_documents.items()
    ):
        session_ref = (
            firestore_db
            .collection("timetables")
            .document(session_id)
        )

        batch.set(
            session_ref,
            document,
            merge=True,
        )

        session_ids.append(session_id)

    publication_ref = (
        firestore_db
        .collection(
            "timetablePublications"
        )
        .document(publication_id)
    )

    publication_document = {
        "publicationId": publication_id,

        "academicYearId": request.academicYearId,
        "academicYear": request.academicYear,

        "semesterId": request.semesterId,
        "semester": request.semester,

        "categoryId": request.categoryId,

        "programmeId": request.programmeId,
        "programmeCode": request.programmeCode,
        "programmeName": request.programmeName,

        "sessionCount": len(
            unique_documents
        ),
        "sessionIds": sorted(
            session_ids
        ),

        "source": "UDOM_RATIBA",
        "status": "PUBLISHED",
        "active": True,

        "publishedAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }

    batch.set(
        publication_ref,
        publication_document,
        merge=True,
    )

    batch.commit()

    return {
        "publicationId": publication_id,
        "sessionCount": len(
            unique_documents
        ),
        "sessionIds": sorted(
            session_ids
        ),
    }


# ---------------------------------------------------------------------
# CACHE AND RELIABLE UDOM REQUESTS
# ---------------------------------------------------------------------

def get_cached_reference(cache_key):
    with _reference_cache_lock:
        cached_item = _reference_cache.get(
            cache_key
        )

        if cached_item is None:
            return None

        if monotonic() >= cached_item[
            "expiresAt"
        ]:
            _reference_cache.pop(
                cache_key,
                None,
            )
            return None

        return cached_item["data"]


def save_cached_reference(
        cache_key,
        data,
):
    with _reference_cache_lock:
        _reference_cache[cache_key] = {
            "expiresAt": (
                monotonic()
                + REFERENCE_CACHE_TTL_SECONDS
            ),
            "data": data,
        }


def request_with_retries(
        session: requests.Session,
        url: str,
        *,
        params=None,
        headers=None,
        attempts: int = 3,
        timeout=DEFAULT_TIMEOUT,
) -> requests.Response:
    last_error = None

    for attempt in range(attempts):
        try:
            response = session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )

            response.raise_for_status()
            return response

        except requests.RequestException as error:
            last_error = error

            if attempt < attempts - 1:
                time.sleep(
                    2 * (attempt + 1)
                )

    if last_error is not None:
        raise last_error

    raise RuntimeError(
        "UDOM request failed without a reported error."
    )


def open_dynamic_udom_session():
    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; "
                "Win64; x64; rv:152.0) "
                "Gecko/20100101 Firefox/152.0"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "*/*;q=0.8"
            ),
            "Connection": "close",
        }
    )

    index_url = (
        f"{BASE_URL}/downloads/index"
    )

    response = request_with_retries(
        session,
        index_url,
        timeout=DEFAULT_TIMEOUT,
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    csrf_token = get_csrf_token(
        soup
    )

    return (
        session,
        index_url,
        soup,
        csrf_token,
    )


def dynamic_ajax_headers(
        index_url: str,
        csrf_token: str,
) -> dict:
    return {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url,
        "Accept": "*/*",
        "Connection": "close",
    }


# ---------------------------------------------------------------------
# DYNAMIC REFERENCE DOWNLOADS
# ---------------------------------------------------------------------

def download_categories(
        year_id: str,
        semester_id: str,
):
    (
        session,
        index_url,
        _,
        csrf_token,
    ) = open_dynamic_udom_session()

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": "",
        "option": "",
        "data": "",
    }

    response = request_with_retries(
        session,
        (
            f"{BASE_URL}"
            "/downloads/fetch-categories"
        ),
        params=params,
        headers=dynamic_ajax_headers(
            index_url,
            csrf_token,
        ),
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    categories = []

    for option in soup.find_all(
            "option"
    ):
        category_id = option.get(
            "value",
            "",
        ).strip()

        category_name = option.get_text(
            " ",
            strip=True,
        )

        if not category_id:
            continue

        categories.append(
            {
                "categoryId": category_id,
                "category": category_name,
            }
        )

    if not categories:
        raise RuntimeError(
            "No timetable categories were found."
        )

    return categories


def download_options(
        year_id: str,
        semester_id: str,
        type_id: str,
):
    (
        session,
        index_url,
        _,
        csrf_token,
    ) = open_dynamic_udom_session()

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": "",
        "data": "",
    }

    response = request_with_retries(
        session,
        f"{BASE_URL}/downloads/opt",
        params=params,
        headers=dynamic_ajax_headers(
            index_url,
            csrf_token,
        ),
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    options = []

    for option in soup.find_all(
            "option"
    ):
        option_id = option.get(
            "value",
            "",
        ).strip()

        option_name = option.get_text(
            " ",
            strip=True,
        )

        if not option_id:
            continue

        options.append(
            {
                "optionId": option_id,
                "option": option_name,
            }
        )

    if not options:
        raise RuntimeError(
            "No timetable download options were found."
        )

    return options


def download_data(
        year_id: str,
        semester_id: str,
        type_id: str,
        option_id: str,
):
    allowed_options = {
        "room",
        "course",
        "programme",
        "instructor",
    }

    if option_id not in allowed_options:
        raise RuntimeError(
            "Invalid option. Use room, course, "
            "programme, or instructor."
        )

    (
        session,
        index_url,
        _,
        csrf_token,
    ) = open_dynamic_udom_session()

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": option_id,
        "data": "",
    }

    response = request_with_retries(
        session,
        f"{BASE_URL}/downloads/data",
        params=params,
        headers=dynamic_ajax_headers(
            index_url,
            csrf_token,
        ),
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    data_select = soup.select_one(
        "select#data"
    )

    if data_select is None:
        raise RuntimeError(
            "No data selector was found for "
            f"option '{option_id}'."
        )

    items = []

    for option in data_select.find_all(
            "option"
    ):
        item_id = option.get(
            "value",
            "",
        ).strip()

        item_name = option.get_text(
            " ",
            strip=True,
        )

        if not item_id:
            continue

        items.append(
            {
                "id": item_id,
                "name": item_name,
            }
        )

    if not items:
        raise RuntimeError(
            "No records were found for "
            f"option '{option_id}'."
        )

    return items


# These definitions intentionally override the old hard-coded helpers
# preserved above. Existing parser code remains untouched.

def download_programmes(
        year_id: str,
        semester_id: str,
        type_id: str,
):
    items = download_data(
        year_id,
        semester_id,
        type_id,
        "programme",
    )

    programmes = []

    for item in items:
        programme_id = item["id"]
        label = item["name"]

        if " - " in label:
            (
                programme_code,
                programme_name,
            ) = label.split(
                " - ",
                1,
            )
        else:
            programme_code = ""
            programme_name = label

        programme_code = (
            programme_code.strip()
        )

        programme_name = (
            programme_name.strip()
        )

        programmes.append(
            {
                "programmeId": programme_id,
                "programmeCode": programme_code,
                "programmeName": programme_name,
                "studyYear": extract_study_year(
                    programme_code
                ),
            }
        )

    return programmes


def download_instructors(
        year_id: str,
        semester_id: str,
        type_id: str,
):
    items = download_data(
        year_id,
        semester_id,
        type_id,
        "instructor",
    )

    instructors = []

    for item in items:
        instructor_id = normalize_whitespace(
            item.get("id")
        )

        instructor_name = normalize_whitespace(
            item.get("name")
        )

        if not instructor_id:
            continue

        instructors.append(
            {
                "instructorId": instructor_id,
                "instructorName": instructor_name,
                "displayLabel": instructor_name,
                # Compatibility aliases.
                "id": instructor_id,
                "name": instructor_name,
            }
        )

    return instructors


# ---------------------------------------------------------------------
# DYNAMIC TIMETABLE DOWNLOADS
# ---------------------------------------------------------------------

def download_timetable_html_by_option(
        data_id: str,
        year_id: str,
        semester_id: str,
        type_id: str,
        option_id: str,
):
    if option_id not in {
        "programme",
        "course",
        "instructor",
    }:
        raise RuntimeError(
            "Invalid timetable option."
        )

    (
        session,
        index_url,
        _,
        csrf_token,
    ) = open_dynamic_udom_session()

    base_params = [
        ("_csrf-backend", csrf_token),
        ("year", year_id),
        ("semester", semester_id),
        ("type", type_id),
        ("option", option_id),
    ]

    parameter_names = (
        ["data"]
        if option_id == "programme"
        else ["data[]", "data"]
    )

    errors = []

    for parameter_name in parameter_names:
        try:
            response = request_with_retries(
                session,
                f"{BASE_URL}/downloads/view",
                params=[
                    *base_params,
                    (parameter_name, data_id),
                ],
                headers=dynamic_ajax_headers(
                    index_url,
                    csrf_token,
                ),
                attempts=1,
                timeout=TIMETABLE_TIMEOUT,
            )

            if response.text.strip():
                return response.text

            errors.append(
                f"{parameter_name}=empty"
            )

        except requests.RequestException as error:
            status_code = (
                error.response.status_code
                if error.response is not None
                else type(error).__name__
            )

            errors.append(
                f"{parameter_name}={status_code}"
            )

    raise RuntimeError(
        "UDOM rejected the "
        f"{option_id} timetable request "
        f"for ID {data_id}. Tried: "
        + ", ".join(errors)
    )


def download_timetable_html(
        programme_id: str,
        year_id: str,
        semester_id: str,
        type_id: str,
):
    return download_timetable_html_by_option(
        programme_id,
        year_id,
        semester_id,
        type_id,
        "programme",
    )


def download_course_timetable_html(
        course_id: str,
        year_id: str,
        semester_id: str,
        type_id: str,
):
    return download_timetable_html_by_option(
        course_id,
        year_id,
        semester_id,
        type_id,
        "course",
    )


def download_instructor_timetable_html(
        instructor_id: str,
        year_id: str,
        semester_id: str,
        type_id: str,
):
    return download_timetable_html_by_option(
        instructor_id,
        year_id,
        semester_id,
        type_id,
        "instructor",
    )


def download_venue_timetable_html(
        venue_ids: list[str],
        year_id: str,
        semester_id: str,
        type_id: str,
):
    if not venue_ids:
        raise RuntimeError(
            "At least one venue ID is required."
        )

    parts = []

    for venue_id in venue_ids:
        (
            session,
            index_url,
            _,
            csrf_token,
        ) = open_dynamic_udom_session()

        response = request_with_retries(
            session,
            f"{BASE_URL}/downloads/view",
            params=[
                ("_csrf-backend", csrf_token),
                ("year", year_id),
                ("semester", semester_id),
                ("type", type_id),
                ("option", "room"),
                ("data[]", venue_id),
            ],
            headers=dynamic_ajax_headers(
                index_url,
                csrf_token,
            ),
            timeout=TIMETABLE_TIMEOUT,
        )

        if not response.text.strip():
            raise RuntimeError(
                "UDOM returned an empty timetable "
                f"for venue {venue_id}."
            )

        parts.append(
            f"""
            <section>
                <h3>Venue ID: {venue_id}</h3>
                {response.text}
            </section>
            """
        )

        time.sleep(1)

    return (
        "<!DOCTYPE html><html><body>"
        + "<hr>".join(parts)
        + "</body></html>"
    )


# ---------------------------------------------------------------------
# PARSER WRAPPERS FOR COURSE, INSTRUCTOR AND VENUE
# ---------------------------------------------------------------------

def parse_course_timetable(
        html: str,
        course_id: str,
):
    result = parse_timetable(
        html,
        course_id,
    )

    return {
        "courseId": course_id,
        "title": result.get(
            "programmeCode",
            "",
        ),
        "category": result.get(
            "category",
            "",
        ),
        "sessionCount": result.get(
            "sessionCount",
            0,
        ),
        "source": result.get(
            "source",
            "UDOM Ratiba",
        ),
        "sessions": result.get(
            "sessions",
            [],
        ),
    }


def parse_instructor_timetable(
        html: str,
        instructor_id: str,
):
    result = parse_timetable(
        html,
        instructor_id,
    )

    return {
        "instructorId": instructor_id,
        "title": result.get(
            "programmeCode",
            "",
        ),
        "category": result.get(
            "category",
            "",
        ),
        "sessionCount": result.get(
            "sessionCount",
            0,
        ),
        "source": result.get(
            "source",
            "UDOM Ratiba",
        ),
        "sessions": result.get(
            "sessions",
            [],
        ),
    }


def parse_venue_timetable(
        html: str,
        venue_ids: list[str],
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    sections = soup.find_all(
        "section"
    )

    if not sections:
        sections = [soup]

    venue_results = []
    all_sessions = []

    for index, section in enumerate(
            sections
    ):
        venue_id = (
            venue_ids[index]
            if index < len(venue_ids)
            else ""
        )

        heading = section.find("h3")

        if heading is not None:
            match = re.search(
                r"Venue\s+ID\s*:\s*(.+)",
                heading.get_text(
                    " ",
                    strip=True,
                ),
                re.IGNORECASE,
            )

            if match:
                venue_id = (
                    match.group(1).strip()
                )

        try:
            parsed = parse_timetable(
                str(section),
                venue_id,
            )

            sessions = parsed.get(
                "sessions",
                [],
            )

            category = parsed.get(
                "category",
                "",
            )

            title = parsed.get(
                "programmeCode",
                "",
            )

        except RuntimeError:
            sessions = []
            category = ""
            title = ""

        decorated_sessions = []

        for session in sessions:
            decorated = {
                "sourceVenueId": venue_id,
                "sourceTitle": title,
                **session,
            }

            decorated_sessions.append(
                decorated
            )

            all_sessions.append(
                decorated
            )

        venue_results.append(
            {
                "venueId": venue_id,
                "title": title,
                "category": category,
                "sessionCount": len(
                    decorated_sessions
                ),
                "sessions": decorated_sessions,
            }
        )

    return {
        "venueIds": venue_ids,
        "venueCount": len(
            venue_results
        ),
        "sessionCount": len(
            all_sessions
        ),
        "source": "UDOM Ratiba",
        "venues": venue_results,
        "sessions": all_sessions,
    }


# ---------------------------------------------------------------------
# COMPLETE API ENDPOINTS
# ---------------------------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "UDOM Ratiba API is running",
        "status": "success",
        "version": app.version,
    }


@app.get("/academic-years")
def get_academic_years():
    cache_key = (
        "academic-years",
    )

    cached = get_cached_reference(
        cache_key
    )

    if cached is not None:
        return {
            "count": len(cached),
            "academicYears": cached,
            "cached": True,
        }

    try:
        years = download_academic_years()

        save_cached_reference(
            cache_key,
            years,
        )

        return {
            "count": len(years),
            "academicYears": years,
            "cached": False,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "UDOM Ratiba servers are "
                f"currently unavailable: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error


@app.get("/semesters/{year_id}")
def get_semesters(
        year_id: str,
):
    cache_key = (
        "semesters",
        year_id,
    )

    cached = get_cached_reference(
        cache_key
    )

    if cached is not None:
        return {
            "academicYearId": year_id,
            "count": len(cached),
            "semesters": cached,
            "cached": True,
        }

    try:
        semesters = download_semesters(
            year_id
        )

        save_cached_reference(
            cache_key,
            semesters,
        )

        return {
            "academicYearId": year_id,
            "count": len(semesters),
            "semesters": semesters,
            "cached": False,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "UDOM Ratiba servers are "
                f"currently unavailable: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error


@app.get("/categories")
def get_categories(
        year_id: str,
        semester_id: str,
):
    try:
        categories = download_categories(
            year_id,
            semester_id,
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "count": len(categories),
            "categories": categories,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error


@app.get("/options")
def get_options(
        year_id: str,
        semester_id: str,
        type_id: str,
):
    try:
        options = download_options(
            year_id,
            semester_id,
            type_id,
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "categoryId": type_id,
            "count": len(options),
            "options": options,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error


@app.get("/data")
def get_data(
        year_id: str,
        semester_id: str,
        type_id: str,
        option: str,
):
    try:
        items = download_data(
            year_id,
            semester_id,
            type_id,
            option,
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "categoryId": type_id,
            "option": option,
            "count": len(items),
            "items": items,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error


@app.get("/programmes")
def get_programmes(
        year_id: str,
        semester_id: str,
        type_id: str = "1",
):
    cache_key = (
        "programmes",
        year_id,
        semester_id,
        type_id,
    )

    cached = get_cached_reference(
        cache_key
    )

    if cached is not None:
        return {
            "count": len(cached),
            "programmes": cached,
            "cached": True,
        }

    try:
        programmes = download_programmes(
            year_id,
            semester_id,
            type_id,
        )

        save_cached_reference(
            cache_key,
            programmes,
        )

        return {
            "count": len(programmes),
            "programmes": programmes,
            "cached": False,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "UDOM Ratiba servers are "
                f"currently unavailable: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error


@app.get("/instructors")
def get_instructors(
        year_id: str = "12",
        semester_id: str = "3368",
        type_id: str = "1",
):
    cache_key = (
        "instructors",
        year_id,
        semester_id,
        type_id,
    )

    cached = get_cached_reference(
        cache_key
    )

    if cached is not None:
        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "categoryId": type_id,
            "count": len(cached),
            "instructors": cached,
            "cached": True,
        }

    try:
        instructors = download_instructors(
            year_id,
            semester_id,
            type_id,
        )

        save_cached_reference(
            cache_key,
            instructors,
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "categoryId": type_id,
            "count": len(instructors),
            "instructors": instructors,
            "cached": False,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        ) from error


@app.get("/timetable/{programme_id}")
def get_live_timetable(
        programme_id: str,
        year_id: str,
        semester_id: str,
        type_id: str = "1",
):
    try:
        html = download_timetable_html(
            programme_id,
            year_id,
            semester_id,
            type_id,
        )

        result = parse_timetable(
            html,
            programme_id,
        )

        result["academicYearId"] = year_id
        result["semesterId"] = semester_id
        result["categoryId"] = type_id

        return result

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get("/course-timetable/{course_id}")
def get_course_timetable(
        course_id: str,
        year_id: str,
        semester_id: str,
        type_id: str = "1",
):
    try:
        html = download_course_timetable_html(
            course_id,
            year_id,
            semester_id,
            type_id,
        )

        result = parse_course_timetable(
            html,
            course_id,
        )

        result["academicYearId"] = year_id
        result["semesterId"] = semester_id
        result["categoryId"] = type_id

        return result

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get(
    "/course-timetable/{course_id}/raw",
    response_class=HTMLResponse,
)
def get_raw_course_timetable(
        course_id: str,
        year_id: str,
        semester_id: str,
        type_id: str = "1",
):
    try:
        html = download_course_timetable_html(
            course_id,
            year_id,
            semester_id,
            type_id,
        )

        return HTMLResponse(
            content=html,
            status_code=200,
        )

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get(
    "/instructor-timetable/{instructor_id}"
)
def get_instructor_timetable(
        instructor_id: str,
        year_id: str,
        semester_id: str,
        type_id: str = "1",
):
    try:
        html = download_instructor_timetable_html(
            instructor_id,
            year_id,
            semester_id,
            type_id,
        )

        result = parse_instructor_timetable(
            html,
            instructor_id,
        )

        result["academicYearId"] = year_id
        result["semesterId"] = semester_id
        result["categoryId"] = type_id

        return result

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get(
    "/instructor-timetable/{instructor_id}/raw",
    response_class=HTMLResponse,
)
def get_raw_instructor_timetable(
        instructor_id: str,
        year_id: str,
        semester_id: str,
        type_id: str = "1",
):
    try:
        html = download_instructor_timetable_html(
            instructor_id,
            year_id,
            semester_id,
            type_id,
        )

        return HTMLResponse(
            content=html,
            status_code=200,
        )

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get("/venue-timetable")
def get_venue_timetable(
        year_id: str,
        semester_id: str,
        type_id: str = "1",
        venue_id: list[str] = Query(...),
):
    try:
        html = download_venue_timetable_html(
            venue_id,
            year_id,
            semester_id,
            type_id,
        )

        result = parse_venue_timetable(
            html,
            venue_id,
        )

        result["academicYearId"] = year_id
        result["semesterId"] = semester_id
        result["categoryId"] = type_id

        return result

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get(
    "/venue-timetable/raw",
    response_class=HTMLResponse,
)
def get_raw_venue_timetable(
        year_id: str,
        semester_id: str,
        type_id: str = "1",
        venue_id: list[str] = Query(...),
):
    try:
        html = download_venue_timetable_html(
            venue_id,
            year_id,
            semester_id,
            type_id,
        )

        return HTMLResponse(
            content=html,
            status_code=200,
        )

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM "
                f"Ratiba: {error}"
            ),
        ) from error

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error


@app.get("/academic-units")
def get_academic_units():
    if academic_unit_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "academic_units.py is missing. "
                "Place it beside main.py."
            ),
        )

    try:
        return (
            academic_unit_service
            .get_academic_units()
        )

    except AcademicUnitServiceError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        ) from error


@app.get(
    "/academic-units/{unit_id}/departments"
)
def get_academic_unit_departments(
        unit_id: str,
):
    if academic_unit_service is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "academic_units.py is missing. "
                "Place it beside main.py."
            ),
        )

    try:
        return (
            academic_unit_service
            .get_departments(
                unit_id
            )
        )

    except AcademicUnitServiceError as error:
        message = str(error)

        status_code = (
            404
            if "was not found" in message
            else 502
        )

        raise HTTPException(
            status_code=status_code,
            detail=message,
        ) from error


@app.get("/firebase-status")
def firebase_status():
    if firestore_db is None:
        return {
            "connected": False,
            "projectId": None,
        }

    firebase_app = firebase_admin.get_app()

    return {
        "connected": True,
        "projectId": firebase_app.project_id,
    }


@app.post("/publish-timetable")
def publish_timetable(
        request: PublishTimetableRequest,
):
    if firestore_db is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Firebase is not configured on "
                "this server. Set "
                "firebase_service_account_b64 "
                "before publishing."
            ),
        )

    publication_id = build_publication_id(
        request
    )

    publication_ref = (
        firestore_db
        .collection(
            "timetablePublications"
        )
        .document(publication_id)
    )

    try:
        existing = publication_ref.get()

        if existing.exists:
            existing_data = (
                existing.to_dict()
                or {}
            )

            return {
                "success": True,
                "alreadyPublished": True,
                "publicationId": publication_id,
                "programmeCode": (
                    request.programmeCode
                ),
                "sessionCount": (
                    existing_data.get(
                        "sessionCount",
                        0,
                    )
                ),
                "message": (
                    "This timetable is already published"
                ),
            }

        html = download_timetable_html(
            request.programmeId,
            request.academicYearId,
            request.semesterId,
            request.categoryId,
        )

        timetable = parse_timetable(
            html,
            request.programmeId,
        )

        sessions = (
            timetable.get("sessions")
            or []
        )

        if not sessions:
            raise HTTPException(
                status_code=404,
                detail=(
                    "UDOM returned no sessions for "
                    "the selected programme"
                ),
            )

        publication_result = (
            write_official_sessions_to_firestore(
                request,
                sessions,
            )
        )

        return {
            "success": True,
            "alreadyPublished": False,
            "programmeCode": (
                request.programmeCode
            ),
            "message": (
                "Official timetable published"
            ),
            **publication_result,
        }

    except HTTPException:
        raise

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM Ratiba "
                f"while publishing: {error}"
            ),
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to publish timetable: "
                f"{error}"
            ),
        ) from error
