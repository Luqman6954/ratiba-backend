import re
import requests

from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException


app = FastAPI(
    title="UDOM Ratiba API",
    description="Fetches and converts UDOM timetable information into JSON",
    version="2.0.0"
)

BASE_URL = "https://ratiba.udom.ac.tz"


def get_csrf_token(soup):
    meta_tag = soup.find("meta", attrs={"name": "csrf-token"})

    if meta_tag and meta_tag.get("content"):
        return meta_tag.get("content")

    hidden_input = soup.find(
        "input",
        attrs={"name": "_csrf-backend"}
    )

    if hidden_input and hidden_input.get("value"):
        return hidden_input.get("value")

    raise RuntimeError("CSRF token was not found.")

def download_academic_years():
    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    response = session.get(
        index_url,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # Find the academic-year selector
    year_select = (
        soup.select_one("select#year")
        or soup.select_one('select[name="year"]')
    )

    if year_select is None:
        raise RuntimeError(
            "Academic-year list was not found."
        )

    academic_years = []

    for option in year_select.find_all("option"):
        year_id = option.get("value", "").strip()
        year_name = option.get_text(" ", strip=True)

        # Ignore empty 'Select Academic Year' option
        if not year_id:
            continue

        academic_years.append({
            "academicYearId": year_id,
            "academicYear": year_name
        })

    return academic_years


def download_semesters(year_id):
    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    # Open UDOM Ratiba and create a public session
    index_response = session.get(
        index_url,
        timeout=30
    )

    index_response.raise_for_status()

    index_soup = BeautifulSoup(
        index_response.text,
        "html.parser"
    )

    csrf_token = get_csrf_token(index_soup)

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": "",
        "type": "",
        "option": "",
        "data": ""
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url
    }

    response = session.get(
        f"{BASE_URL}/downloads/fetch-semesters",
        params=params,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    semesters = []

    for option in soup.find_all("option"):
        semester_id = option.get("value", "").strip()
        semester_name = option.get_text(" ", strip=True)

        # Ignore the empty "Select Semester" option
        if not semester_id:
            continue

        semesters.append({
            "semesterId": semester_id,
            "semester": semester_name
        })

    if not semesters:
        raise RuntimeError(
            f"No semesters were found for academic year ID {year_id}."
        )

    return semesters



def download_categories(year_id, semester_id):
    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    index_response = session.get(
        index_url,
        timeout=30
    )

    index_response.raise_for_status()

    index_soup = BeautifulSoup(
        index_response.text,
        "html.parser"
    )

    csrf_token = get_csrf_token(index_soup)

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": "",
        "option": "",
        "data": ""
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url
    }

    response = session.get(
        f"{BASE_URL}/downloads/fetch-categories",
        params=params,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    categories = []

    for option in soup.find_all("option"):
        category_id = option.get("value", "").strip()
        category_name = option.get_text(" ", strip=True)

        if not category_id:
            continue

        categories.append({
            "categoryId": category_id,
            "category": category_name
        })

    if not categories:
        raise RuntimeError(
            "No timetable categories were found."
        )

    return categories

def download_options(
    year_id,
    semester_id,
    type_id
):
    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    index_response = session.get(
        index_url,
        timeout=30
    )

    index_response.raise_for_status()

    index_soup = BeautifulSoup(
        index_response.text,
        "html.parser"
    )

    csrf_token = get_csrf_token(index_soup)

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": "",
        "data": ""
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url
    }

    response = session.get(
        f"{BASE_URL}/downloads/opt",
        params=params,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    options = []

    for option in soup.find_all("option"):
        option_id = option.get("value", "").strip()
        option_name = option.get_text(" ", strip=True)

        if not option_id:
            continue

        options.append({
            "optionId": option_id,
            "option": option_name
        })

    if not options:
        raise RuntimeError(
            "No timetable download options were found."
        )

    return options


def download_data(
    year_id,
    semester_id,
    type_id,
    option_id
):
    allowed_options = {
        "room",
        "course",
        "programme",
        "instructor"
    }

    if option_id not in allowed_options:
        raise RuntimeError(
            "Invalid option. Use room, course, programme, or instructor."
        )

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    index_response = session.get(
        index_url,
        timeout=30
    )

    index_response.raise_for_status()

    index_soup = BeautifulSoup(
        index_response.text,
        "html.parser"
    )

    csrf_token = get_csrf_token(index_soup)

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": option_id,
        "data": ""
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url
    }

    response = session.get(
        f"{BASE_URL}/downloads/data",
        params=params,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

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

        items.append({
            "id": item_id,
            "name": item_name
        })

    if not items:
        raise RuntimeError(
            f"No records were found for option '{option_id}'."
        )

    return items


def download_programmes(
    year_id,
    semester_id,
    type_id
):
    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    index_response = session.get(
        index_url,
        timeout=30
    )

    index_response.raise_for_status()

    index_soup = BeautifulSoup(
        index_response.text,
        "html.parser"
    )

    csrf_token = get_csrf_token(index_soup)

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": "programme",
        "data": ""
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url
    }

    response = session.get(
        f"{BASE_URL}/downloads/data",
        params=params,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    programme_select = soup.select_one("select#data")

    if programme_select is None:
        raise RuntimeError(
            "Programme list was not found."
        )

    programmes = []

    for option in programme_select.find_all("option"):
        programme_id = option.get("value", "").strip()
        label = option.get_text(" ", strip=True)

        if not programme_id:
            continue

        if " - " in label:
            programme_code, programme_name = label.split(
                " - ",
                1
            )
        else:
            programme_code = ""
            programme_name = label

        programmes.append({
            "programmeId": programme_id,
            "programmeCode": programme_code.strip(),
            "programmeName": programme_name.strip()
        })

    return programmes

def download_timetable_html(
    programme_id,
    year_id,
    semester_id,
    type_id
):
    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Ratiba-API/1.0"
        )
    })

    index_url = f"{BASE_URL}/downloads/index"

    index_response = session.get(
        index_url,
        timeout=30
    )

    index_response.raise_for_status()

    index_soup = BeautifulSoup(
        index_response.text,
        "html.parser"
    )

    csrf_token = get_csrf_token(index_soup)

    params = {
        "_csrf-backend": csrf_token,
        "year": year_id,
        "semester": semester_id,
        "type": type_id,
        "option": "programme",
        "data": programme_id
    }

    headers = {
        "X-CSRF-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": index_url
    }

    timetable_response = session.get(
        f"{BASE_URL}/downloads/view",
        params=params,
        headers=headers,
        timeout=30
    )

    timetable_response.raise_for_status()

    return timetable_response.text


def parse_timetable(html, programme_id):
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find("h4")

    if heading is None:
        raise RuntimeError(
            "Timetable heading was not found."
        )

    heading_text = heading.get_text(
        " ",
        strip=True
    )

    heading_match = re.search(
        r"(.+?)\s*-\s*(.+?)\s+Timetable",
        heading_text,
        re.IGNORECASE
    )

    programme_code = ""
    category = ""

    if heading_match:
        programme_code = heading_match.group(1).strip()
        category = heading_match.group(2).title().strip()

    course_names = {}

    description_span = soup.find(
        "span",
        string=lambda text: (
            text
            and "DESCRIPTION" in text.upper()
        )
    )

    if description_span:
        description_table = description_span.find_next(
            "table"
        )

        if description_table:
            for row in description_table.find_all("tr"):
                columns = row.find_all("td")

                if len(columns) >= 3:
                    course_code = columns[1].get_text(
                        " ",
                        strip=True
                    )

                    course_name = columns[2].get_text(
                        " ",
                        strip=True
                    )

                    course_code = " ".join(
                        course_code.split()
                    )

                    course_name = course_name.lstrip(
                        "-"
                    ).strip()

                    if course_code:
                        course_names[course_code] = course_name

    timetable_table = soup.select_one(
        "table.responsive-sm"
    )

    if timetable_table is None:
        raise RuntimeError(
            "Main timetable table was not found."
        )

    weekdays = {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday"
    }

    sessions = []

    for row in timetable_table.select("tbody > tr"):
        cells = row.find_all(
            "td",
            recursive=False
        )

        if not cells:
            continue

        day = cells[0].get_text(
            " ",
            strip=True
        )

        if day not in weekdays:
            continue

        for cell in cells[1:]:
            course_tag = cell.find("i")

            if course_tag is None:
                continue

            full_text = cell.get_text(
                " ",
                strip=True
            )

            if (
                "Staff" not in full_text
                or "Venue" not in full_text
            ):
                continue

            time_match = re.search(
                r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
                full_text
            )

            if not time_match:
                continue

            start_time = time_match.group(1)
            end_time = time_match.group(2)

            course_session_text = course_tag.get_text(
                " ",
                strip=True
            )

            if " - " in course_session_text:
                course_part, session_type = (
                    course_session_text.rsplit(
                        " - ",
                        1
                    )
                )
            else:
                course_part = course_session_text
                session_type = ""

            course_part = " ".join(
                course_part.split()
            )

            session_type = session_type.strip()

            course_code = ""
            group = ""

            sorted_codes = sorted(
                course_names.keys(),
                key=len,
                reverse=True
            )

            for known_code in sorted_codes:
                if course_part.startswith(known_code):
                    course_code = known_code

                    group = course_part[
                        len(known_code):
                    ].strip()

                    break

            if not course_code:
                course_code = course_part

            staff_match = re.search(
                r"Staff\s*:\s*(.*?)\s*;\s*Students",
                full_text,
                re.IGNORECASE
            )

            lecturer_name = (
                staff_match.group(1).strip()
                if staff_match
                else ""
            )

            students_match = re.search(
                r"Students\s*:\s*(.*?)\s*;\s*Venue",
                full_text,
                re.IGNORECASE
            )

            student_groups = []

            if students_match:
                student_groups = [
                    student.strip()
                    for student
                    in students_match.group(1).split(",")
                    if student.strip()
                ]

            venue_match = re.search(
                r"Venue\s*:\s*(.+)$",
                full_text,
                re.IGNORECASE
            )

            venue = (
                venue_match.group(1).strip()
                if venue_match
                else ""
            )

            sessions.append({
                "programmeCode": programme_code,
                "category": category,
                "day": day,
                "startTime": start_time,
                "endTime": end_time,
                "courseCode": course_code,
                "courseName": course_names.get(
                    course_code,
                    ""
                ),
                "group": group,
                "sessionType": session_type,
                "lecturerName": lecturer_name,
                "studentGroups": student_groups,
                "venue": venue
            })

    return {
        "programmeId": programme_id,
        "programmeCode": programme_code,
        "category": category,
        "sessionCount": len(sessions),
        "source": "UDOM Ratiba",
        "sessions": sessions
    }


@app.get("/")
def home():
    return {
        "message": "UDOM Ratiba API is running",
        "status": "success"
    }

@app.get("/academic-years")
def get_academic_years():
    try:
        academic_years = download_academic_years()

        return {
            "count": len(academic_years),
            "academicYears": academic_years
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

@app.get("/semesters/{year_id}")
def get_semesters(year_id: str):
    try:
        semesters = download_semesters(year_id)

        return {
            "academicYearId": year_id,
            "count": len(semesters),
            "semesters": semesters
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        )

@app.get("/categories")
def get_categories(
    year_id: str,
    semester_id: str
):
    try:
        categories = download_categories(
            year_id,
            semester_id
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "count": len(categories),
            "categories": categories
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        )

@app.get("/options")
def get_options(
    year_id: str,
    semester_id: str,
    type_id: str
):
    try:
        options = download_options(
            year_id,
            semester_id,
            type_id
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "categoryId": type_id,
            "count": len(options),
            "options": options
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        )

@app.get("/data")
def get_data(
    year_id: str,
    semester_id: str,
    type_id: str,
    option: str
):
    try:
        items = download_data(
            year_id,
            semester_id,
            type_id,
            option
        )

        return {
            "academicYearId": year_id,
            "semesterId": semester_id,
            "categoryId": type_id,
            "option": option,
            "count": len(items),
            "items": items
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error)
        )

@app.get("/programmes")
def get_programmes(
    year_id: str,
    semester_id: str,
    type_id: str = "1"
):
    try:
        programmes = download_programmes(
            year_id,
            semester_id,
            type_id
        )

        return {
            "count": len(programmes),
            "programmes": programmes
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

@app.get("/timetable/{programme_id}")
def get_live_timetable(
    programme_id: str,
    year_id: str,
    semester_id: str,
    type_id: str = "1"
):
    try:
        html = download_timetable_html(
            programme_id,
            year_id,
            semester_id,
            type_id
        )

        timetable = parse_timetable(
            html,
            programme_id
        )

        return timetable

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Could not contact UDOM Ratiba: {error}"
        )

    except RuntimeError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
