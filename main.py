import re
import time
from threading import Lock
from time import monotonic

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

import firebase_admin
from firebase_admin import firestore
from firebase_admin import credentials
from pydantic import BaseModel

import hashlib
import json

import os
import base64




app = FastAPI(
    title="UDOM Ratiba API",
    description="Fetches and converts UDOM timetable information into JSON",
    version="2.2.0",
)

BASE_URL = "https://ratiba.udom.ac.tz"
DEFAULT_TIMEOUT = (20, 120)
VENUE_TIMEOUT = (20, 180)

# Reference information such as academic years,
# semesters and programmes does not change frequently.
REFERENCE_CACHE_TTL_SECONDS = 30 * 60

_reference_cache = {}
_reference_cache_lock = Lock()


def initialize_firestore():

    try:
        firebase_app = firebase_admin.get_app()

    except ValueError:

        encoded_credentials = os.getenv(
    "firebase_service_account_b64"
)

        if not encoded_credentials:
            raise RuntimeError(
               "firebase_service_account_b64"
                "environment variable is missing"
            )

        try:
            decoded_credentials = base64.b64decode(
                encoded_credentials
            ).decode(
                "utf-8"
            )

            service_account_info = json.loads(
                decoded_credentials
            )

        except Exception as error:
            raise RuntimeError(
                "Unable to decode Firebase "
                "service account credentials"
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
    """
    Converts text into a safe Firestore document-ID section.
    """

    value = value.strip()

    return re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        value
    ).strip("-")


def build_publication_id(
        request: PublishTimetableRequest
) -> str:
    """
    Creates one stable ID for a programme timetable publication.
    """

    return "_".join([
        clean_document_id_part(request.academicYearId),
        clean_document_id_part(request.semesterId),
        clean_document_id_part(request.categoryId),
        clean_document_id_part(request.programmeId)
    ])



def normalize_session_value(value) -> str:
    """
    Produces consistent text for session identity comparison.
    """

    if value is None:
        return ""

    return " ".join(
        str(value).strip().upper().split()
    )

def build_official_session_id(
        request: PublishTimetableRequest,
        session: dict
) -> str:
    """
    Creates a stable Firestore ID for one official UDOM session.

    The same shared session returned under different programmes
    will produce the same document ID.
    """

    student_groups = session.get(
        "studentGroups"
    ) or []

    normalized_groups = sorted({
        normalize_session_value(group)
        for group in student_groups
        if normalize_session_value(group)
    })

    session_identity = {
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
        "studentGroups": normalized_groups
    }

    serialized_identity = json.dumps(
        session_identity,
        sort_keys=True,
        separators=(",", ":")
    )

    session_hash = hashlib.sha256(
        serialized_identity.encode("utf-8")
    ).hexdigest()

    return f"udom_{session_hash[:32]}"



def clean_display_text(value) -> str:
    """
    Cleans text while preserving its original letter case.
    """

    if value is None:
        return ""

    return " ".join(
        str(value).strip().split()
    )


def clean_student_groups(values) -> list[str]:
    """
    Removes empty and duplicate programme codes.
    """

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
        key=str.upper
    )

def build_firestore_session_document(
        request: PublishTimetableRequest,
        session: dict,
        session_id: str
) -> dict:
    """
    Converts one normalized UDOM session into a Firestore document.
    """

    student_groups = clean_student_groups(
        session.get("studentGroups")
    )

    # Ensure the programme requesting the timetable
    # is included even when UDOM omits studentGroups.
    requested_programme_code = clean_display_text(
        request.programmeCode
    )

    existing_group_keys = {
        group.upper()
        for group in student_groups
    }

    if (
            requested_programme_code
            and requested_programme_code.upper()
            not in existing_group_keys
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
        # Stable identifiers
        "firebaseId": session_id,
        "officialSessionId": session_id,
        "publicationId": publication_id,

        # Academic period
        "academicYearId": request.academicYearId,
        "academicYear": request.academicYear,
        "semesterId": request.semesterId,
        "semester": request.semester,
        "categoryId": request.categoryId,
        "category": clean_display_text(
            session.get("category")
        ) or "Teaching",

        # Programme information
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

        # Existing Android compatibility fields
        "department": "",
        "course": request.programmeName,
        "year": year_label,

        # Session information
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

        # Timetable state
        "sessionStatus": "Pending",
        "source": "UDOM_RATIBA",
        "official": True,
        "active": True,

        # Existing reminder fields
        "oneHourReminderSent": False,
        "lecturerReminderSent": False,
        "crReminderSent": False,
        "crFollowUpReminderSent": False,

        # Server timestamp
        "updatedAt": firestore.SERVER_TIMESTAMP
    }


def write_official_sessions_to_firestore(
        request: PublishTimetableRequest,
        sessions: list[dict]
) -> dict:
    """
    Writes all official sessions and the publication record
    into Firestore using one controlled batch operation.
    """

    if not sessions:
        raise ValueError(
            "Cannot publish an empty timetable"
        )

    publication_id = build_publication_id(
        request
    )

    # Store sessions by stable ID to prevent duplicate
    # writes inside the same Firestore batch.
    unique_session_documents = {}

    for session in sessions:

        session_id = build_official_session_id(
            request,
            session
        )

        session_document = (
            build_firestore_session_document(
                request,
                session,
                session_id
            )
        )

        
        # Shared sessions may be imported through more than
# one programme. ArrayUnion preserves programme
# identities already attached to the session.
         
        session_document["programmeIds"] = (
            firestore.ArrayUnion([
                request.programmeId
            ])
        )

        session_document["programmeCodes"] = (
            firestore.ArrayUnion([
                request.programmeCode
            ])
        )

        session_document["programmeNames"] = (
            firestore.ArrayUnion([
                request.programmeName
            ])
        )

        unique_session_documents[
            session_id
        ] = session_document

    batch = firestore_db.batch()

    session_ids = []

    for session_id, document in (
            unique_session_documents.items()
    ):

        session_ref = (
            firestore_db
            .collection("timetables")
            .document(session_id)
        )

        batch.set(
            session_ref,
            document,
            merge=True
        )

        session_ids.append(
            session_id
        )

    publication_ref = (
        firestore_db
        .collection("timetablePublications")
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
            unique_session_documents
        ),

        "sessionIds": sorted(
            session_ids
        ),

        "source": "UDOM_RATIBA",
        "status": "PUBLISHED",
        "active": True,

        "publishedAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP
    }

    batch.set(
        publication_ref,
        publication_document,
        merge=True
    )

    batch.commit()

    return {
        "publicationId": publication_id,
        "sessionCount": len(
            unique_session_documents
        ),
        "sessionIds": sorted(
            session_ids
        )
    }

def get_cached_reference(cache_key):
    """
    Return cached data when it exists and has not expired.
    """

    with _reference_cache_lock:
        cached_item = _reference_cache.get(cache_key)

        if cached_item is None:
            return None

        expires_at = cached_item["expiresAt"]

        if monotonic() >= expires_at:
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
    """
    Save successful reference data temporarily.
    """

    with _reference_cache_lock:
        _reference_cache[cache_key] = {
            "expiresAt": (
                monotonic()
                + REFERENCE_CACHE_TTL_SECONDS
            ),
            "data": data,
        }



def get_csrf_token(soup: BeautifulSoup) -> str:
    """Read the CSRF token from the public UDOM Ratiba page."""
    meta_tag = soup.find("meta", attrs={"name": "csrf-token"})

    if meta_tag and meta_tag.get("content"):
        return str(meta_tag.get("content"))

    hidden_input = soup.find("input", attrs={"name": "_csrf-backend"})

    if hidden_input and hidden_input.get("value"):
        return str(hidden_input.get("value"))

    raise RuntimeError("CSRF token was not found.")


def request_with_retries(
    session: requests.Session,
    url: str,
    *,
    params=None,
    headers=None,
    attempts: int = 3,
    timeout=DEFAULT_TIMEOUT,
) -> requests.Response:
    """Send a GET request and retry temporary UDOM connection failures."""
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
                time.sleep(2 * (attempt + 1))

    if last_error is not None:
        raise last_error

    raise RuntimeError("UDOM request failed without a reported error.")


def open_udom_session():
    """Open the public downloads page and return a ready session."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) "
                "Gecko/20100101 Firefox/152.0"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        }
    )

    index_url = f"{BASE_URL}/downloads/index"

    index_response = request_with_retries(
        session,
        index_url,
        timeout=DEFAULT_TIMEOUT,
    )

    index_soup = BeautifulSoup(index_response.text, "html.parser")
    csrf_token = get_csrf_token(index_soup)

    return session, index_url, index_soup, csrf_token


def ajax_headers(index_url: str, csrf_token: str) -> dict:
    """Headers used by the public UDOM Ratiba AJAX requests."""
    return {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url,
        "Accept": "*/*",
    }


def download_academic_years():
    _, _, index_soup, _ = open_udom_session()

    year_select = (
        index_soup.select_one("select#year")
        or index_soup.select_one('select[name="year"]')
    )

    if year_select is None:
        raise RuntimeError("Academic-year list was not found.")

    academic_years = []

    for option in year_select.find_all("option"):
        year_id = option.get("value", "").strip()
        year_name = option.get_text(" ", strip=True)

        if not year_id:
            continue

        academic_years.append(
            {
                "academicYearId": year_id,
                "academicYear": year_name,
            }
        )

    if not academic_years:
        raise RuntimeError("No academic years were found.")

    return academic_years


def download_semesters(year_id: str):
    session, index_url, _, csrf_token = open_udom_session()

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": "",
        "type": "",
        "option": "",
        "data": "",
    }

    response = request_with_retries(
        session,
        f"{BASE_URL}/downloads/fetch-semesters",
        params=params,
        headers=ajax_headers(index_url, csrf_token),
        timeout=DEFAULT_TIMEOUT,
    )

    soup = BeautifulSoup(response.text, "html.parser")
    semesters = []

    for option in soup.find_all("option"):
        semester_id = option.get("value", "").strip()
        semester_name = option.get_text(" ", strip=True)

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
            f"No semesters were found for academic year ID {year_id}."
        )

    return semesters


def download_categories(year_id: str, semester_id: str):
    session, index_url, _, csrf_token = open_udom_session()

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
        f"{BASE_URL}/downloads/fetch-categories",
        params=params,
        headers=ajax_headers(index_url, csrf_token),
        timeout=DEFAULT_TIMEOUT,
    )

    soup = BeautifulSoup(response.text, "html.parser")
    categories = []

    for option in soup.find_all("option"):
        category_id = option.get("value", "").strip()
        category_name = option.get_text(" ", strip=True)

        if not category_id:
            continue

        categories.append(
            {
                "categoryId": category_id,
                "category": category_name,
            }
        )

    if not categories:
        raise RuntimeError("No timetable categories were found.")

    return categories


def download_options(
    year_id: str,
    semester_id: str,
    type_id: str,
):
    session, index_url, _, csrf_token = open_udom_session()

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
        headers=ajax_headers(index_url, csrf_token),
        timeout=DEFAULT_TIMEOUT,
    )

    soup = BeautifulSoup(response.text, "html.parser")
    options = []

    for option in soup.find_all("option"):
        option_id = option.get("value", "").strip()
        option_name = option.get_text(" ", strip=True)

        if not option_id:
            continue

        options.append(
            {
                "optionId": option_id,
                "option": option_name,
            }
        )

    if not options:
        raise RuntimeError("No timetable download options were found.")

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
            "Invalid option. Use room, course, programme, or instructor."
        )

    session, index_url, _, csrf_token = open_udom_session()

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
        headers=ajax_headers(index_url, csrf_token),
        timeout=DEFAULT_TIMEOUT,
    )

    soup = BeautifulSoup(response.text, "html.parser")
    data_select = soup.select_one("select#data")

    if data_select is None:
        raise RuntimeError(
            f"No data selector was found for option '{option_id}'."
        )

    items = []

    for option in data_select.find_all("option"):
        item_id = option.get("value", "").strip()
        item_name = option.get_text(" ", strip=True)

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
            f"No records were found for option '{option_id}'."
        )

    return items



def extract_study_year(programme_code: str):
    """
    Extract the last number found in the programme code.

    Examples:
        IS3             -> 3
        BIS2            -> 2
        MBA(EVENING)2   -> 2
        MSCCS1(DE)      -> 1
    """

    if not programme_code:
        return None

    match = re.search(
        r"(\d+)(?!.*\d)",
        programme_code,
    )

    if match is None:
        return None

    return int(match.group(1))



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
            programme_code, programme_name = label.split(
                " - ",
                1,
            )
        else:
            programme_code = ""
            programme_name = label

        clean_programme_code = programme_code.strip()
        clean_programme_name = programme_name.strip()

        programmes.append(
            {
                "programmeId": programme_id,
                "programmeCode": clean_programme_code,
                "programmeName": clean_programme_name,
                "studyYear": extract_study_year(
                    clean_programme_code
                ),
            }
        )

    return programmes


def download_timetable_html(
    programme_id: str,
    year_id: str,
    semester_id: str,
    type_id: str,
):
    session, index_url, _, csrf_token = open_udom_session()

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": "programme",
        "data": programme_id,
    }

    response = request_with_retries(
        session,
        f"{BASE_URL}/downloads/view",
        params=params,
        headers=ajax_headers(index_url, csrf_token),
        timeout=VENUE_TIMEOUT,
    )

    if not response.text.strip():
        raise RuntimeError("UDOM returned an empty programme timetable.")

    return response.text


def download_venue_timetable_html(
    venue_ids: list[str],
    year_id: str,
    semester_id: str,
    type_id: str,
):
    if not venue_ids:
        raise RuntimeError("At least one venue ID is required.")

    timetable_parts = []

    # UDOM can time out when several venues are sent together.
    # Fetch each venue separately and combine the returned HTML.
    for venue_id in venue_ids:
        session, index_url, _, csrf_token = open_udom_session()

        params = [
            ("_csrf-backend", csrf_token),
            ("year", year_id),
            ("semester", semester_id),
            ("type", type_id),
            ("option", "room"),
            ("data[]", venue_id),
        ]

        headers = ajax_headers(index_url, csrf_token)
        headers["Connection"] = "close"

        response = request_with_retries(
            session,
            f"{BASE_URL}/downloads/view",
            params=params,
            headers=headers,
            timeout=VENUE_TIMEOUT,
        )

        if not response.text.strip():
            raise RuntimeError(
                f"UDOM returned an empty timetable for venue {venue_id}."
            )

        timetable_parts.append(
            f"""
            <section>
                <h3>Venue ID: {venue_id}</h3>
                {response.text}
            </section>
            """
        )

        time.sleep(1)

    return (
        """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Venue Timetables</title>
        </head>
        <body>
        """
        + "<hr>".join(timetable_parts)
        + """
        </body>
        </html>
        """
    )


def parse_timetable(html: str, programme_id: str):
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find("h4")

    if heading is None:
        raise RuntimeError("Timetable heading was not found.")

    heading_text = heading.get_text(" ", strip=True)

    heading_match = re.search(
        r"(.+?)\s*-\s*(.+?)\s+Timetable",
        heading_text,
        re.IGNORECASE,
    )

    programme_code = ""
    category = ""

    if heading_match:
        programme_code = heading_match.group(1).strip()
        category = heading_match.group(2).title().strip()

    course_names = {}

    description_span = soup.find(
        "span",
        string=lambda text: text and "DESCRIPTION" in text.upper(),
    )

    if description_span:
        description_table = description_span.find_next("table")

        if description_table:
            for row in description_table.find_all("tr"):
                columns = row.find_all("td")

                if len(columns) >= 3:
                    course_code = columns[1].get_text(" ", strip=True)
                    course_name = columns[2].get_text(" ", strip=True)

                    course_code = " ".join(course_code.split())
                    course_name = course_name.lstrip("-").strip()

                    if course_code:
                        course_names[course_code] = course_name

    timetable_table = soup.select_one("table.responsive-sm")

    if timetable_table is None:
        raise RuntimeError("Main timetable table was not found.")

    weekdays = {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }

    sessions = []

    for row in timetable_table.select("tbody > tr"):
        cells = row.find_all("td", recursive=False)

        if not cells:
            continue

        day = cells[0].get_text(" ", strip=True)

        if day not in weekdays:
            continue

        for cell in cells[1:]:
            course_tag = cell.find("i")

            if course_tag is None:
                continue

            full_text = cell.get_text(" ", strip=True)

            if "Staff" not in full_text or "Venue" not in full_text:
                continue

            time_match = re.search(
                r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
                full_text,
            )

            if not time_match:
                continue

            start_time = time_match.group(1)
            end_time = time_match.group(2)
            course_session_text = course_tag.get_text(" ", strip=True)

            if " - " in course_session_text:
                course_part, session_type = course_session_text.rsplit(
                    " - ",
                    1,
                )
            else:
                course_part = course_session_text
                session_type = ""

            course_part = " ".join(course_part.split())
            session_type = session_type.strip()
            course_code = ""
            group = ""

            sorted_codes = sorted(
                course_names.keys(),
                key=len,
                reverse=True,
            )

            for known_code in sorted_codes:
                if course_part.startswith(known_code):
                    course_code = known_code
                    group = course_part[len(known_code) :].strip()
                    break

            if not course_code:
                course_code = course_part

            staff_match = re.search(
                r"Staff\s*:\s*(.*?)\s*;\s*Students",
                full_text,
                re.IGNORECASE,
            )

            lecturer_name = (
                staff_match.group(1).strip() if staff_match else ""
            )

            students_match = re.search(
                r"Students\s*:\s*(.*?)\s*;\s*Venue",
                full_text,
                re.IGNORECASE,
            )

            student_groups = []

            if students_match:
                student_groups = [
                    student.strip()
                    for student in students_match.group(1).split(",")
                    if student.strip()
                ]

            venue_match = re.search(
                r"Venue\s*:\s*(.+)$",
                full_text,
                re.IGNORECASE,
            )

            venue = venue_match.group(1).strip() if venue_match else ""

            sessions.append(
                {
                    "programmeCode": programme_code,
                    "category": category,
                    "day": day,
                    "startTime": start_time,
                    "endTime": end_time,
                    "courseCode": course_code,
                    "courseName": course_names.get(course_code, ""),
                    "group": group,
                    "sessionType": session_type,
                    "lecturerName": lecturer_name,
                    "studentGroups": student_groups,
                    "venue": venue,
                }
            )

    return {
        "programmeId": programme_id,
        "programmeCode": programme_code,
        "category": category,
        "sessionCount": len(sessions),
        "source": "UDOM Ratiba",
        "sessions": sessions,
    }



def extract_course_names(scope) -> dict[str, str]:
    """Extract course-code and course-name pairs from a timetable section."""
    course_names: dict[str, str] = {}

    description_span = scope.find(
        "span",
        string=lambda value: value and "DESCRIPTION" in value.upper(),
    )

    if description_span is None:
        return course_names

    description_table = description_span.find_next("table")

    if description_table is None:
        return course_names

    for row in description_table.find_all("tr"):
        columns = row.find_all("td")

        if len(columns) < 3:
            continue

        course_code = " ".join(
            columns[1].get_text(" ", strip=True).split()
        )
        course_name = columns[2].get_text(" ", strip=True)
        course_name = course_name.lstrip("-").strip()

        if course_code:
            course_names[course_code] = course_name

    return course_names


def parse_venue_timetable(html: str, venue_ids: list[str]):
    """Convert one or more raw venue timetables into JSON-ready data."""
    soup = BeautifulSoup(html, "html.parser")
    sections = soup.find_all("section", recursive=True)

    # A direct UDOM response may not have our wrapper section.
    if not sections:
        sections = [soup]

    weekdays = {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }

    all_sessions = []
    venue_results = []

    for section_index, section in enumerate(sections):
        source_venue_id = (
            venue_ids[section_index]
            if section_index < len(venue_ids)
            else ""
        )

        venue_heading = section.find("h3")

        if venue_heading:
            heading_match = re.search(
                r"Venue\s+ID\s*:\s*(.+)",
                venue_heading.get_text(" ", strip=True),
                re.IGNORECASE,
            )

            if heading_match:
                source_venue_id = heading_match.group(1).strip()

        course_names = extract_course_names(section)
        section_sessions = []
        timetable_tables = section.select("table.responsive-sm")
        title = ""
        category = ""

        for timetable_table in timetable_tables:
            heading = timetable_table.find_previous("h4")

            if heading and heading.find_parent("section") == section:
                title = heading.get_text(" ", strip=True)

                category_match = re.search(
                    r"-\s*(.+?)\s+Timetable$",
                    title,
                    re.IGNORECASE,
                )

                if category_match:
                    category = category_match.group(1).title().strip()

            for row in timetable_table.select("tbody > tr"):
                cells = row.find_all("td", recursive=False)

                if not cells:
                    continue

                day = cells[0].get_text(" ", strip=True)

                if day not in weekdays:
                    continue

                for cell in cells[1:]:
                    course_tag = cell.find("i")

                    if course_tag is None:
                        continue

                    full_text = cell.get_text(" ", strip=True)

                    if "Staff" not in full_text or "Venue" not in full_text:
                        continue

                    time_match = re.search(
                        r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
                        full_text,
                    )

                    if not time_match:
                        continue

                    course_session_text = course_tag.get_text(
                        " ",
                        strip=True,
                    )

                    if " - " in course_session_text:
                        course_part, session_type = (
                            course_session_text.rsplit(" - ", 1)
                        )
                    else:
                        course_part = course_session_text
                        session_type = ""

                    course_part = " ".join(course_part.split())
                    session_type = session_type.strip()
                    course_code = ""
                    group = ""

                    for known_code in sorted(
                        course_names,
                        key=len,
                        reverse=True,
                    ):
                        if course_part.startswith(known_code):
                            course_code = known_code
                            group = course_part[len(known_code) :].strip()
                            break

                    if not course_code:
                        course_code = course_part

                    staff_match = re.search(
                        r"Staff\s*:\s*(.*?)\s*;\s*Students",
                        full_text,
                        re.IGNORECASE,
                    )
                    lecturer_name = (
                        staff_match.group(1).strip()
                        if staff_match
                        else ""
                    )

                    students_match = re.search(
                        r"Students\s*:\s*(.*?)\s*;\s*Venue",
                        full_text,
                        re.IGNORECASE,
                    )
                    student_groups = []

                    if students_match:
                        student_groups = [
                            student.strip()
                            for student in students_match.group(1).split(",")
                            if student.strip()
                        ]

                    venue_match = re.search(
                        r"Venue\s*:\s*(.+)$",
                        full_text,
                        re.IGNORECASE,
                    )
                    venue_name = (
                        venue_match.group(1).strip()
                        if venue_match
                        else ""
                    )

                    session = {
                        "sourceVenueId": source_venue_id,
                        "sourceTitle": title,
                        "category": category,
                        "day": day,
                        "startTime": time_match.group(1),
                        "endTime": time_match.group(2),
                        "courseCode": course_code,
                        "courseName": course_names.get(course_code, ""),
                        "group": group,
                        "sessionType": session_type,
                        "lecturerName": lecturer_name,
                        "studentGroups": student_groups,
                        "venue": venue_name,
                    }

                    section_sessions.append(session)
                    all_sessions.append(session)

        venue_results.append(
            {
                "venueId": source_venue_id,
                "title": title,
                "category": category,
                "sessionCount": len(section_sessions),
                "sessions": section_sessions,
            }
        )

    return {
        "venueIds": venue_ids,
        "venueCount": len(venue_results),
        "sessionCount": len(all_sessions),
        "source": "UDOM Ratiba",
        "venues": venue_results,
        "sessions": all_sessions,
    }


@app.get("/")
def home():
    return {
        "message": "UDOM Ratiba API is running",
        "status": "success",
    }


@app.get("/academic-years")
def get_academic_years():
    cache_key = (
        "academic-years",
    )

    cached_academic_years = get_cached_reference(
        cache_key
    )

    if cached_academic_years is not None:
        return {
            "count": len(cached_academic_years),
            "academicYears": cached_academic_years,
            "cached": True,
        }

    try:
        academic_years = download_academic_years()

        save_cached_reference(
            cache_key,
            academic_years,
        )

        return {
            "count": len(academic_years),
            "academicYears": academic_years,
            "cached": False,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "UDOM Ratiba servers are currently "
                f"unavailable: {error}"
            ),
        )

    except RuntimeError as error:
        raise HTTPException( status_code=503,detail=str(error),)


@app.get("/semesters/{year_id}")
def get_semesters(
    year_id: str,
):
    cache_key = (
        "semesters",
        year_id,
    )

    cached_semesters = get_cached_reference(
        cache_key
    )

    if cached_semesters is not None:
        return {
            "academicYearId": year_id,
            "count": len(cached_semesters),
            "semesters": cached_semesters,
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
                "UDOM Ratiba servers are currently "
                f"unavailable: {error}"
            ),
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        )


@app.get("/categories")
def get_categories(
    year_id: str,
    semester_id: str,
):
    try:
        categories = download_categories(year_id, semester_id)

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "count": len(categories),
            "categories": categories,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}",
        )

    except RuntimeError as error:
        raise HTTPException(status_code=404, detail=str(error))


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
            detail=f"Could not contact UDOM Ratiba: {error}",
        )

    except RuntimeError as error:
        raise HTTPException(status_code=404, detail=str(error))


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
            detail=f"Could not contact UDOM Ratiba: {error}",
        )

    except RuntimeError as error:
        raise HTTPException(status_code=404, detail=str(error))


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

    cached_programmes = get_cached_reference(
        cache_key
    )

    if cached_programmes is not None:
        return {
            "count": len(cached_programmes),
            "programmes": cached_programmes,
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
                "UDOM Ratiba servers are currently "
                f"unavailable: {error}"
            ),
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
        )


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

        return parse_timetable(html, programme_id)

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}",
        )

    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error))



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

        result = parse_venue_timetable(html, venue_id)
        result["academicYearId"] = year_id
        result["semesterId"] = semester_id
        result["categoryId"] = type_id

        return result

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}",
        )

    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error))


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

        return HTMLResponse(content=html, status_code=200)

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}",
        )

    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.get("/firebase-status")
def firebase_status():
    firebase_app = firebase_admin.get_app()

    return {
        "connected": True,
        "projectId": firebase_app.project_id
    }




@app.post("/publish-timetable")
def publish_timetable(
    request: PublishTimetableRequest
):
    publication_id = build_publication_id(request)

    publication_ref = (
        firestore_db
        .collection("timetablePublications")
        .document(publication_id)
    )

    try:
        # Prevent the same programme timetable
        # from being published repeatedly.
        existing_publication = publication_ref.get()

        if existing_publication.exists:
            existing_data = (
                existing_publication.to_dict()
                or {}
            )

            return {
                "success": True,
                "alreadyPublished": True,
                "publicationId": publication_id,
                "programmeCode": request.programmeCode,
                "sessionCount": existing_data.get(
                    "sessionCount",
                    0
                ),
                "message": (
                    "This timetable is already published"
                )
            }

        # Download the official timetable from UDOM.
        html = download_timetable_html(
            request.programmeId,
            request.academicYearId,
            request.semesterId,
            request.categoryId
        )

        # Convert the downloaded timetable into
        # normalized session dictionaries.
        timetable_result = parse_timetable(
            html,
            request.programmeId
        )

        sessions = (
            timetable_result.get("sessions")
            or []
        )

        if not sessions:
            raise HTTPException(
                status_code=404,
                detail=(
                    "UDOM returned no sessions for "
                    "the selected programme"
                )
            )

        # Publish sessions and the publication record
        # into Firestore.
        publication_result = (
            write_official_sessions_to_firestore(
                request,
                sessions
            )
        )

        return {
            "success": True,
            "alreadyPublished": False,
            "publicationId": publication_result[
                "publicationId"
            ],
            "programmeCode": request.programmeCode,
            "sessionCount": publication_result[
                "sessionCount"
            ],
            "message": (
                "Official UDOM timetable published "
                "successfully"
            )
        }

    except HTTPException:
        raise

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not contact UDOM Ratiba: "
                f"{error}"
            )
        )

    except (RuntimeError, ValueError) as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not publish timetable: "
                f"{error}"
            )
        )