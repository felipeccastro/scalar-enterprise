# Tests

A handful of high-level tests, each telling the story of one real user
journey through the app — not unit tests, and deliberately not many of
them. Each file runs the real app against a throwaway database and drives
it the way a person would: sign up, fill in a form, click through, see
what's on the page.

The intent is that someone who isn't a programmer can read a test file
top to bottom, as a script of what a specific person did and what they
should have seen — no mocks, no fixtures full of jargon, no framework
beyond Python's own standard library (this app has a zero-pip-dependency
rule; these tests keep it).

## Running them

```bash
python3 tests/run_all.py          # every journey
python3 tests/test_getting_started_journey.py -v   # just one, verbose
```

`-v` on a single file prints each test method's one-line docstring next to
its result — that's the readable summary of what was checked.

## What's here

- `test_getting_started_journey.py` — signing up, seeing sample data, and
  getting back in (or not) on the next visit.
- `test_client_relationship_journey.py` — taking a client from lead to
  signed, with a task and a comment along the way, then archiving them.
- `test_audit_trail_journey.py` — every change to a record is written
  down, and a password is never one of the things written down.

## Adding one

Give it its own file (`test_*.py`), subclass `Journey` from `_harness.py`,
and write **one** test method that reads as a continuous story — see
`_harness.py`'s own docstring for why one file gets one boot of the app
and, in turn, one journey rather than several independent ones.
