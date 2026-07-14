import json
import re
from bs4 import BeautifulSoup


# Open the timetable HTML file we downloaded earlier
with open("is2_timetable.html", "r", encoding="utf-8") as file:
    html = file.read()

soup = BeautifulSoup(html, "html.parser")


# Read the timetable heading
heading = soup.find("h4")

if heading is None:
    print("Error: Timetable heading was not found.")
    exit()

heading_text = heading.get_text(" ", strip=True)

print("Reading:", heading_text)


# Extract programme and timetable category
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


# Read course codes and full course names
course_names = {}

description_span = soup.find(
    "span",
    string=lambda text: text and "DESCRIPTION" in text.upper()
)

if description_span:
    description_table = description_span.find_next("table")

    if description_table:
        for row in description_table.find_all("tr"):
            columns = row.find_all("td")

            if len(columns) >= 3:
                course_code = columns[1].get_text(" ", strip=True)
                course_name = columns[2].get_text(" ", strip=True)

                course_name = course_name.lstrip("-").strip()

                if course_code:
                    course_names[course_code] = course_name


# Find the main timetable table
timetable_table = soup.select_one("table.responsive-sm")

if timetable_table is None:
    print("Error: Main timetable table was not found.")
    exit()


sessions = []

weekdays = {
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday"
}


# Read each timetable row
for row in timetable_table.select("tbody > tr"):
    cells = row.find_all("td", recursive=False)

    if not cells:
        continue

    day = cells[0].get_text(" ", strip=True)

    if day not in weekdays:
        continue

    # Check all remaining cells for real sessions
    for cell in cells[1:]:
        course_tag = cell.find("i")

        if course_tag is None:
            continue

        full_text = cell.get_text(" ", strip=True)

        if "Staff" not in full_text or "Venue" not in full_text:
            continue

        # Extract session time
        time_match = re.search(
            r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})",
            full_text
        )

        if not time_match:
            continue

        start_time = time_match.group(1)
        end_time = time_match.group(2)

        # Example: CP 221 F - Practical
        course_session_text = course_tag.get_text(" ", strip=True)

        if " - " in course_session_text:
            course_part, session_type = course_session_text.rsplit(
                " - ",
                1
            )
        else:
            course_part = course_session_text
            session_type = ""

        course_part = " ".join(course_part.split())
        session_type = session_type.strip()

        # Find the course code from the description list
        course_code = ""
        group = ""

        sorted_codes = sorted(
            course_names.keys(),
            key=len,
            reverse=True
        )

        for known_code in sorted_codes:
            clean_known_code = " ".join(known_code.split())

            if course_part.startswith(clean_known_code):
                course_code = clean_known_code
                group = course_part[len(clean_known_code):].strip()
                break

        if not course_code:
            course_code = course_part

        # Extract lecturer
        staff_match = re.search(
            r"Staff\s*:\s*(.*?)\s*;\s*Students",
            full_text,
            re.IGNORECASE
        )

        lecturer = staff_match.group(1).strip() if staff_match else ""

        # Extract student groups
        students_match = re.search(
            r"Students\s*:\s*(.*?)\s*;\s*Venue",
            full_text,
            re.IGNORECASE
        )

        student_groups = []

        if students_match:
            student_groups = [
                student.strip()
                for student in students_match.group(1).split(",")
                if student.strip()
            ]

        # Extract venue
        venue_match = re.search(
            r"Venue\s*:\s*(.+)$",
            full_text,
            re.IGNORECASE
        )

        venue = venue_match.group(1).strip() if venue_match else ""

        session = {
            "programmeCode": programme_code,
            "category": category,
            "day": day,
            "startTime": start_time,
            "endTime": end_time,
            "courseCode": course_code,
            "courseName": course_names.get(course_code, ""),
            "group": group,
            "sessionType": session_type,
            "lecturerName": lecturer,
            "studentGroups": student_groups,
            "venue": venue
        }

        sessions.append(session)


# Create the final JSON structure
result = {
    "programmeCode": programme_code,
    "category": category,
    "sessionCount": len(sessions),
    "sessions": sessions
}


# Save everything into a JSON file
with open("is2_timetable.json", "w", encoding="utf-8") as file:
    json.dump(result, file, indent=4, ensure_ascii=False)


print("Success!")
print("Sessions found:", len(sessions))
print("Saved as: is2_timetable.json")