#!/usr/bin/env python3

import argparse
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore


def normalize_course_key(value):
    return "".join(
        str(value or "").split()
    ).upper()


def init_firebase():
    if firebase_admin._apps:
        return

    configured = os.environ.get(
        "FIREBASE_SERVICE_ACCOUNT_PATH"
    )

    credential_path = (
        Path(configured).expanduser()
        if configured
        else Path.home()
        / ".config"
        / "ratiba"
        / "firebase-admin.json"
    )

    if not credential_path.exists():
        raise SystemExit(
            "Firebase service account file not found"
        )

    firebase_admin.initialize_app(
        credentials.Certificate(
            str(credential_path)
        )
    )


def assign(args):
    db = firestore.client()

    course = args.course.strip()
    year = args.year.strip()
    semester = args.semester.strip()

    if not course:
        raise SystemExit("course is required")

    if not year:
        raise SystemExit("year is required")

    if not semester:
        raise SystemExit("semester is required")

    data = {
        "uid": args.uid,
        "role": "CR",
        "active": True,

        # Canonical timetable/resource scope.
        "course": course,
        "courseKey": normalize_course_key(
            course
        ),
        "programmeCode": course,
        "year": year,
        "semester": semester,

        # Official academic identifiers.
        "academicYearId":
            str(args.academic_year_id),
        "semesterId":
            str(args.semester_id),

        "assignmentSource":
            "ADMIN",
        "updatedAt":
            firestore.SERVER_TIMESTAMP,
    }

    ref = (
        db.collection("cr_assignments")
        .document(args.uid)
    )

    snapshot = ref.get()

    if not snapshot.exists:
        data["createdAt"] = (
            firestore.SERVER_TIMESTAMP
        )

    ref.set(
        data,
        merge=True
    )

    print("CR assignment saved.")
    print("course:", course)
    print("year:", year)
    print("semester:", semester)
    print(
        "academicYearId:",
        args.academic_year_id
    )
    print(
        "semesterId:",
        args.semester_id
    )


def revoke(args):
    db = firestore.client()

    ref = (
        db.collection("cr_assignments")
        .document(args.uid)
    )

    snapshot = ref.get()

    if not snapshot.exists:
        print("CR assignment does not exist.")
        return

    ref.set(
        {
            "active": False,
            "updatedAt":
                firestore.SERVER_TIMESTAMP,
            "revokedAt":
                firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    print("CR assignment revoked.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Admin-only Ratiba CR assignment tool"
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True
    )

    assign_parser = subparsers.add_parser(
        "assign"
    )

    assign_parser.add_argument(
        "--uid",
        required=True
    )

    assign_parser.add_argument(
        "--course",
        required=True,
        help="Exact Ratiba programme/class code, e.g. IS2"
    )

    assign_parser.add_argument(
        "--year",
        required=True
    )

    assign_parser.add_argument(
        "--semester",
        required=True
    )

    assign_parser.add_argument(
        "--academic-year-id",
        required=True
    )

    assign_parser.add_argument(
        "--semester-id",
        required=True
    )

    assign_parser.set_defaults(
        handler=assign
    )

    revoke_parser = subparsers.add_parser(
        "revoke"
    )

    revoke_parser.add_argument(
        "--uid",
        required=True
    )

    revoke_parser.set_defaults(
        handler=revoke
    )

    args = parser.parse_args()

    init_firebase()

    args.handler(args)


if __name__ == "__main__":
    main()
