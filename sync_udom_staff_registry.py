from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time

import requests

import firebase_admin
from firebase_admin import credentials, firestore

import main as ratiba_main
AUTO_MATCH_THRESHOLD = 0.94

from udom_staff_registry import (
    academic_units_compatible,
    best_instructor_matches,
    create_session,
    crawl_staff_index,
    extract_instructor_unit,
    fetch_staff_profile,
    normalize_academic_unit_code,
    normalize_institutional_email,
    normalize_person_name,
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
        if score >= AUTO_MATCH_THRESHOLD
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

    # Final identity-shape safety guard.
    #
    # Name-only auto-provisioning is allowed when:
    #   1. first and last names agree, even if middle names are omitted, OR
    #   2. the same exact name tokens are merely reordered.
    #
    # If those structural signals disagree, a matching academic unit
    # is required. Otherwise the record stays for manual review.
    staff_normalized = normalize_person_name(
        staff_name
    )
    instructor_normalized = normalize_person_name(
        instructor_name
    )

    staff_tokens = staff_normalized.split()
    instructor_tokens = instructor_normalized.split()

    if not staff_tokens or not instructor_tokens:
        return None

    same_first = (
        staff_tokens[0] == instructor_tokens[0]
    )
    same_last = (
        staff_tokens[-1] == instructor_tokens[-1]
    )
    same_token_set = (
        set(staff_tokens) == set(instructor_tokens)
    )

    safe_name_only_shape = (
        (same_first and same_last)
        or same_token_set
    )

    if not safe_name_only_shape:
        if not academic_units_compatible(
            staff_unit,
            instructor_unit,
        ):
            return None

        match_basis = "NAME+ACADEMIC_UNIT"

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



def fetch_staff_profile_with_retry(
    session,
    entry,
    retries: int,
):
    """Fetch one UDOM staff profile with conservative retries."""

    attempts = max(1, retries + 1)

    for attempt in range(1, attempts + 1):
        try:
            return fetch_staff_profile(session, entry)

        except requests.RequestException as error:
            if attempt >= attempts:
                raise

            wait_seconds = min(
                15.0,
                3.0 * attempt,
            )

            print(
                f"WARN temporary UDOM request failure "
                f"for {entry.full_name} "
                f"(attempt {attempt}/{attempts}); "
                f"retrying in {wait_seconds:.1f}s: "
                f"{error}",
                file=sys.stderr,
            )

            time.sleep(wait_seconds)


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
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N shortlisted staff profiles",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds to wait after processing each profile",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Additional retries for temporary UDOM request failures",
    )

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

    all_shortlisted = shortlist_entries_for_instructors(
        staff_entries,
        instructor_names,
    )

    offset = max(0, args.offset)
    shortlisted = all_shortlisted[offset:]

    if args.max_profiles > 0:
        shortlisted = shortlisted[:args.max_profiles]

    print(
        f"Total staff profiles shortlisted: "
        f"{len(all_shortlisted)}"
    )
    print(
        f"Processing slice: offset={offset}, "
        f"count={len(shortlisted)}"
    )

    prepared: dict[str, dict] = {}
    ambiguous: list[str] = []
    no_email: list[str] = []
    failures: list[str] = []

    session = create_session()
    try:
        for index, entry in enumerate(shortlisted, start=1):
            try:
                profile = fetch_staff_profile_with_retry(
                    session,
                    entry,
                    args.retries,
                )
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
                failures.append(entry.full_name)
                print(
                    f"WARN {entry.full_name}: {error}",
                    file=sys.stderr,
                )
            finally:
                if args.delay > 0:
                    time.sleep(args.delay)
    finally:
        session.close()

    print("\n--- Phase 1B registry result ---")
    print(f"Prepared institutional-email records: {len(prepared)}")
    print(f"Ambiguous/unmatched profiles: {len(ambiguous)}")
    print(f"Shortlisted profiles without @udom.ac.tz email: {len(no_email)}")
    print(f"Profile fetch/process failures: {len(failures)}")

    if failures:
        print("\nFailed profiles:")
        for item in failures[:20]:
            print(f"  {item}")

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

    if failures:
        raise RuntimeError(
            "Refusing Firestore write because "
            f"{len(failures)} profile(s) failed to process. "
            "Re-run this batch until the failure count is zero."
        )

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
