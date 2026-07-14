import requests
from bs4 import BeautifulSoup

# Create one browser-like session
session = requests.Session()

# Open the public UDOM Ratiba page
index_url = "https://ratiba.udom.ac.tz/downloads/index"

print("Opening UDOM Ratiba...")

page = session.get(index_url, timeout=30)
page.raise_for_status()

# Read the CSRF token from the page
soup = BeautifulSoup(page.text, "html.parser")
csrf_tag = soup.find("meta", attrs={"name": "csrf-token"})

if csrf_tag is None:
    print("Error: CSRF token was not found.")
    exit()

csrf_token = csrf_tag.get("content")

# Request the IS2 teaching timetable
params = {
    "_csrf-backend": csrf_token,
    "year": "12",
    "semester": "3368",
    "type": "1",
    "option": "programme",
    "data": "18071"
}

headers = {
    "X-CSRF-Token": csrf_token,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": index_url
}

print("Downloading IS2 timetable...")

response = session.get(
    "https://ratiba.udom.ac.tz/downloads/view",
    params=params,
    headers=headers,
    timeout=30
)

response.raise_for_status()

# Save the timetable as an HTML file
with open("is2_timetable.html", "w", encoding="utf-8") as file:
    file.write(response.text)

print("Success!")
print("The timetable was saved as is2_timetable.html")