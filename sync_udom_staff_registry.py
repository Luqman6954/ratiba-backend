from __future__ import annotations

import argparse
import base64
import json
import os
import sys

import firebase_admin
from firebase_admin import credentials, firestore

import main as ratiba_main
from udom_staff_registry import (
    academic_units_compatible,
    best_instructor_matches,
    create_session,
    crawl_staff_index,
    extract_instructor_unit,
    fetch_staff_profile,
    normalize_academic_unit_code,
    normalize_institutional_email,
    shortlist_entries_for_instructors,
)


def initialize_firestore():
    try:
        app = firebase_admin.get_app()
    except ValueError:
        encoded = os.getenv("firebase_service_account_b64")
        if not encoded:
            raise RuntimeError(
                "firebase_service_account_b64 is required only when --write is used"
            )
        service_account = json.loads(base64.b64decode(encoded).decode("utf-8"))
        app = firebase_admin.initialize_app(credentials.Certificate(service_account))
    return firestore.client(app=app)


def build_registry_document(
    profile: dict,
    instructors: list[dict],
) -> tuple[str, dict] | None:
    staff_name = profile.get("fullName", "")
    ranked = best_instructor_matches(staff_name, instructors)
    if not ranked:
        return None

    strong = [
        (score, instructor)
        for score, instructor in ranked
        if score >= 0.86
    ]
    if not strong:
        return None

    best_score, best = strong[0]
    second_score = strong[1][0] if len(strong) > 1 else 0.0

    staff_unit = normalize_academic_unit_code(
        profile.get("academicUnitCode", "")
    )

    # If name-only matching produces two nearly identical candidates,
    # resolve the identity using the official UDOM academic unit.
    ambiguous_name = (
        second_score >= 0.80
        and best_score - second_score < 0.05
    )

    match_basis = "NAME"

    if ambiguous_name:
        unit_compatible = []

        for score, instructor in strong:
            instructor_name = str(
                instructor.get("instructorName")
                or instructor.get("name")
                or ""
            ).strip()

            instructor_unit = extract_instructor_unit(
                instructor_name
            )

            if academic_units_compatible(
                staff_unit,
                instructor_unit,
            ):
                unit_compatible.append(
                    (score, instructor)
                )

        if not unit_compatible:
            return None

        unit_compatible.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        best_score, best = unit_compatible[0]
        unit_second_score = (
            unit_compatible[1][0]
            if len(unit_compatible) > 1
            else 0.0
        )

        # Even unit-aware matching must still result in one clear identity.
        if (
            len(unit_compatible) > 1
            and unit_second_score >= 0.80
            and best_score - unit_second_score < 0.05
        ):
            return None

        match_basis = "NAME+ACADEMIC_UNIT"

    emails = [
        normalize_institutional_email(email)
        for email in profile.get("officialEmails") or []
        if normalize_institutional_email(email).endswith("@udom.ac.tz")
    ]
    if not emails:
        return None

    instructor_id = str(
        best.get("instructorId")
        or best.get("id")
        or ""
    ).strip()

    instructor_name = str(
        best.get("instructorName")
        or best.get("name")
        or ""
    ).strip()

    if not instructor_id or not instructor_name:
        return None

    instructor_unit = extract_instructor_unit(
        instructor_name
    )

    payload = {
        "fullName": staff_name,
        "profileUrl": profile.get("profileUrl", ""),
        "availabilityStatus": profile.get("availabilityStatus", ""),
        "department": profile.get("department", ""),
        "academicUnitCode": staff_unit,
        "instructorId": instructor_id,
        "instructorName": instructor_name,
        "instructorUnitCode": instructor_unit,
        "matchScore": best_score,
        "matchBasis": match_basis,
        "active": True,
        "source": "UDOM_PUBLIC_STAFF_DIRECTORY+UDOM_RATIBA",
    }

    return "|".join(sorted(set(emails))), payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare/sync the UDOM staff registry used by Ratiba lecturer auto-provisioning"
    )
    parser.add_argument("--year-id", required=True)
    parser.add_argument("--semester-id", required=True)
    parser.add_argument("--type-id", default="1")
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--max-profiles", type=int, default=0,
                        help="For testing only: stop after N shortlisted profiles (0 = all)")
    parser.add_argument("--write", action="store_true",
                        help="Actually write Firestore. Without this flag the command is a dry run.")
    args = parser.parse_args()

    print("Fetching official Ratiba instructors...")
    instructors = ratiba_main.download_instructors(
        args.year_id,
        args.semester_id,
        args.type_id,
    )
    print(f"Official Ratiba instructors: {len(instructors)}")

    instructor_names = [
        item.get("instructorName") or item.get("name") or ""
        for item in instructors
    ]

    print("Reading UDOM public staff index...")
    staff_entries = crawl_staff_index(max_pages=max(1, args.max_pages))
    print(f"UDOM staff index entries: {len(staff_entries)}")

    shortlisted = shortlist_entries_for_instructors(staff_entries, instructor_names)
    if args.max_profiles > 0:
        shortlisted = shortlisted[:args.max_profiles]
    print(f"Staff profiles shortlisted: {len(shortlisted)}")

    prepared: dict[str, dict] = {}
    ambiguous: list[str] = []
    no_email: list[str] = []

    session = create_session()
    try:
        for index, entry in enumerate(shortlisted, start=1):
            try:
                profile = fetch_staff_profile(session, entry)
                result = build_registry_document(profile, instructors)
                if result is None:
                    ranked = best_instructor_matches(profile.get("fullName", ""), instructors)
                    best = ranked[0][0] if ranked else 0.0
                    if not profile.get("officialEmails"):
                        no_email.append(profile.get("fullName") or entry.full_name)
                    else:
                        ambiguous.append(f"{profile.get('fullName') or entry.full_name} (best={best:.3f})")
                    continue

                email_key, payload = result
                for email in email_key.split("|"):
                    prepared[email] = {
                        **payload,
                        "institutionalEmail": email,
                    }

                unit_note = (
                    f" unit={payload.get('academicUnitCode')}"
                    if payload.get("academicUnitCode")
                    else ""
                )

                print(
                    f"[{index}/{len(shortlisted)}] MATCH "
                    f"{payload['fullName']} -> {payload['instructorName']} "
                    f"({payload['matchScore']:.3f}) "
                    f"[{payload.get('matchBasis', 'NAME')}{unit_note}]"
                )
            except Exception as error:
                print(f"WARN {entry.full_name}: {error}", file=sys.stderr)
    finally:
        session.close()

    print("\n--- Phase 1B registry result ---")
    print(f"Prepared institutional-email records: {len(prepared)}")
    print(f"Ambiguous/unmatched profiles: {len(ambiguous)}")
    print(f"Shortlisted profiles without @udom.ac.tz email: {len(no_email)}")

    print("\nSample prepared records:")
    for email, payload in list(sorted(prepared.items()))[:20]:
        print(
            f"  {email} => {payload['fullName']} => "
            f"{payload['instructorName']} [{payload['instructorId']}] "
            f"score={payload['matchScore']:.3f}"
        )

    if ambiguous:
        print("\nSample ambiguous/unmatched:")
        for item in ambiguous[:20]:
            print(f"  {item}")

    if not args.write:
        print("\nDRY RUN COMPLETE. Firestore was NOT modified.")
        return 0

    if not prepared:
        raise RuntimeError("Refusing to write: no verified registry records were prepared")

    db = initialize_firestore()
    batch = db.batch()
    pending = 0
    written = 0

    for email, payload in sorted(prepared.items()):
        ref = db.collection("udom_staff_registry").document(email)
        batch.set(
            ref,
            {
                **payload,
                "syncedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        pending += 1
        written += 1

        if pending >= 400:
            batch.commit()
            batch = db.batch()
            pending = 0

    if pending:
        batch.commit()

    db.collection("system_metadata").document("udom_staff_registry").set(
        {
            "academicYearId": args.year_id,
            "semesterId": args.semester_id,
            "categoryId": args.type_id,
            "instructorCount": len(instructors),
            "staffIndexCount": len(staff_entries),
            "shortlistedCount": len(shortlisted),
            "registryRecordCount": written,
            "lastSyncedAt": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )

    print(f"\nFirestore sync complete: {written} registry records written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
