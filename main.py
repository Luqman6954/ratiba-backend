import re

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException


app = FastAPI(
    title="UDOM Ratiba API",
    description="Fetches and converts UDOM timetable information into JSON",
    version="2.1.1",
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


SESSION_RECORD_START = "[[SESSION_RECORD_START]]"
SESSION_RECORD_END = "[[SESSION_RECORD_END]]"

SESSION_RECORD_PATTERN = re.compile(
    re.escape(SESSION_RECORD_START)
    + r"\s*(?P<course>.*?)\s*"
    + re.escape(SESSION_RECORD_END),
    re.DOTALL,
)

FIELD_LABEL_PATTERN = re.compile(
    r"(?P<label>Staff|Students|Venue)\s*:\s*",
    re.IGNORECASE,
)


def build_marked_cell_text(cell) -> str:
    """
    Preserve each UDOM <i> course/session block as a hard boundary
    before the cell is flattened into plain text.
    """

    import copy

    cell_copy = copy.deepcopy(cell)

    for course_tag in cell_copy.find_all("i"):
        course_tag.insert_before(
            f" {SESSION_RECORD_START} "
        )
        course_tag.insert_after(
            f" {SESSION_RECORD_END} "
        )

    return normalize_whitespace(
        cell_copy.get_text(
            " ",
            strip=True,
        )
    )


def extract_exact_session_fields(
    record_text: str,
):
    """
    Extract exactly one Staff, Students and Venue field.

    If another UDOM session leaks into this record, duplicate field
    labels appear and the record is rejected instead of returning
    polluted data.
    """

    matches = list(
        FIELD_LABEL_PATTERN.finditer(
            record_text
        )
    )

    counts = {
        "staff": 0,
        "students": 0,
        "venue": 0,
    }

    for match in matches:
        label = (
            match.group("label")
            .lower()
        )

        counts[label] += 1

    if any(
        count != 1
        for count in counts.values()
    ):
        return None

    fields = {}

    for index, match in enumerate(
        matches
    ):
        label = (
            match.group("label")
            .lower()
        )

        value_start = match.end()

        if index + 1 < len(matches):
            value_end = matches[
                index + 1
            ].start()
        else:
            value_end = len(record_text)

        fields[label] = (
            normalize_whitespace(
                record_text[
                    value_start:value_end
                ]
            )
            .strip(" ;")
        )

    return fields


def parse_sessions_from_cell(
    cell,
    course_names: dict,
):
    """
    Parse every session in one timetable cell.

    Time/day identifies the active timetable slot.
    Each <i> element identifies one individual course/session record.
    Staff, Students and Venue are extracted only inside that record.
    """

    marked_text = build_marked_cell_text(
        cell
    )

    if (
        "Staff" not in marked_text
        or "Students" not in marked_text
        or "Venue" not in marked_text
    ):
        return []

    start_matches = list(
        SESSION_START_PATTERN.finditer(
            marked_text
        )
    )

    record_matches = list(
        SESSION_RECORD_PATTERN.finditer(
            marked_text
        )
    )

    if (
        not start_matches
        or not record_matches
    ):
        return []

    parsed_sessions = []

    for index, record_match in enumerate(
        record_matches
    ):
        active_start_match = None

        for start_match in start_matches:
            if (
                start_match.start()
                < record_match.start()
            ):
                active_start_match = start_match
            else:
                break

        if active_start_match is None:
            continue

        next_record_start = (
            record_matches[index + 1].start()
            if index + 1 < len(record_matches)
            else len(marked_text)
        )

        next_time_start = len(marked_text)

        for start_match in start_matches:
            if (
                start_match.start()
                > record_match.end()
            ):
                next_time_start = (
                    start_match.start()
                )
                break

        record_end = min(
            next_record_start,
            next_time_start,
        )

        record_text = marked_text[
            record_match.end():record_end
        ]

        fields = extract_exact_session_fields(
            record_text
        )

        if fields is None:
            continue

        course_session_text = (
            normalize_whitespace(
                record_match.group(
                    "course"
                )
            )
        )

        if " - " in course_session_text:
            (
                course_part,
                session_type,
            ) = course_session_text.rsplit(
                " - ",
                1,
            )
        else:
            course_part = course_session_text
            session_type = ""

        course_code, group = (
            split_course_and_group(
                course_part,
                course_names,
            )
        )

        students_text = fields[
            "students"
        ]

        student_groups = [
            normalize_whitespace(student)
            for student in students_text.split(
                ","
            )
            if normalize_whitespace(student)
        ]

        parsed_sessions.append(
            {
                "day": (
                    active_start_match
                    .group("day")
                    .title()
                    .strip()
                ),
                "startTime": (
                    active_start_match
                    .group("start")
                    .strip()
                ),
                "endTime": (
                    active_start_match
                    .group("end")
                    .strip()
                ),
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
                "lecturerName": fields[
                    "staff"
                ],
                "studentGroups": student_groups,
                "venue": fields[
                    "venue"
                ],
            }
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


@app.get("/")
def home():
    return {
        "message": "UDOM Ratiba API is running",
        "status": "success",
    }


@app.get("/academic-years")
def get_academic_years():
    try:
        academic_years = (
            download_academic_years()
        )

        return {
            "count": len(academic_years),
            "academicYears": academic_years,
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
            status_code=500,
            detail=str(error),
        ) from error


@app.get("/semesters/{year_id}")
def get_semesters(
    year_id: str,
):
    try:
        semesters = download_semesters(
            year_id
        )

        return {
            "academicYearId": year_id,
            "count": len(semesters),
            "semesters": semesters,
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
def get_programmes():
    try:
        programmes = download_programmes()

        return {
            "count": len(programmes),
            "programmes": programmes,
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
            status_code=500,
            detail=str(error),
        ) from error


@app.get("/timetable/{programme_id}")
def get_live_timetable(
    programme_id: str,
):
    try:
        html = download_timetable_html(
            programme_id
        )

        timetable = parse_timetable(
            html,
            programme_id,
        )

        return timetable

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
