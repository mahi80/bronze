"""The timestamp parser against every format and junk token found in the supplied file."""
import datetime as dt

import pytest

CASES = [
    ("2024-07-25T18:27:44", "iso_t", "second", dt.datetime(2024, 7, 25, 18, 27, 44)),
    ("2024-10-24 14:32:10", "iso_space", "second", dt.datetime(2024, 10, 24, 14, 32, 10)),
    ("2024-04-15", "iso_date", "day", dt.datetime(2024, 4, 15)),
    ("02/14/2025 08:16 AM", "us_12h", "minute", dt.datetime(2025, 2, 14, 8, 16)),
    ("06/26/2025 12:35 PM", "us_12h", "minute", dt.datetime(2025, 6, 26, 12, 35)),
    ("12/01/2024 12:05 AM", "us_12h", "minute", dt.datetime(2024, 12, 1, 0, 5)),
    ("02/03/2025", "us_date", "day", dt.datetime(2025, 2, 3)),          # month first: 3 February
    ("28-Feb-2024 17:03", "dd_mon", "minute", dt.datetime(2024, 2, 28, 17, 3)),
    ("03-20-2024 20:39:48", "us_dash", "second", dt.datetime(2024, 3, 20, 20, 39, 48)),
    ("1726547768", "epoch_s", "second", dt.datetime(2024, 9, 17, 4, 36, 8)),
]


@pytest.mark.parametrize("value, fmt, precision, expected", CASES)
def test_registered_formats(conn, value, fmt, precision, expected):
    row = conn.execute("SELECT silver.ts_format(%s), silver.ts_precision(silver.ts_format(%s)), silver.parse_ts(%s)", (value, value, value)).fetchone()
    assert row == (fmt, precision, expected)


@pytest.mark.parametrize("value", ["asap", "TBD", "not a date", "00/00/0000", "", "???", "never", "pending",
                                   "02/30/2024", "13/01/2024", "2025/07/01 10:00", "2024-13-01"])
def test_junk_and_impossible_dates_are_null(conn, value):
    assert conn.execute("SELECT silver.parse_ts(%s)", (value,)).fetchone()[0] is None


def test_parsing_does_not_depend_on_session_time_zone(conn):
    conn.execute("SET LOCAL TIME ZONE 'Asia/Kolkata'")
    naive, epoch, epoch_local = conn.execute(
        "SELECT silver.parse_ts('2024-07-25T18:27:44'), silver.parse_ts('1704049383'), silver.parse_ts('1704049383', 'Asia/Kolkata')").fetchone()
    assert naive == dt.datetime(2024, 7, 25, 18, 27, 44)
    assert epoch == dt.datetime(2023, 12, 31, 19, 3, 3)            # UTC by default
    assert epoch_local == dt.datetime(2024, 1, 1, 0, 33, 3)        # SOURCE_TZ shifts epoch values only


def test_every_valid_created_at_in_the_file_parses(conn):
    unparsed = conn.execute("SELECT count(*) FROM silver.v_bronze_checked WHERE created_at IS NULL "
                            "AND NOT ('unparseable_created_at' = ANY (reject_reasons))").fetchone()[0]
    assert unparsed == 0


@pytest.mark.parametrize("description, building, template", [
    ("Elevator H-011 stuck between floors 1 and 9. No one inside (confirmed).", "Warehouse South",
     "Elevator <ASSET> stuck between floors <N> and <N>. No one inside (confirmed)."),
    ("Faucet in HQ Floor 3 kitchen won't turn off completely. Constant drip.", "HQ Floor 3",
     "Faucet in <BLDG> kitchen won't turn off completely. Constant drip."),
    ("Please check the HVAC — multiple complaints from staff on 7th floor about humidity.", "Annex West",
     "Please check the HVAC — multiple complaints from staff on <N> floor about humidity."),
])
def test_description_masking(conn, description, building, template):
    assert conn.execute("SELECT silver.mask_description(%s, %s)", (description, building)).fetchone()[0] == template
