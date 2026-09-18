"""Journey: can the team trust that every change to a record is written
down somewhere, and that writing it down never exposes something private
like a password?
"""

from __future__ import annotations

import unittest

from _harness import Journey


class AuditTrailJourney(Journey):

    def test_every_change_is_recorded_without_ever_exposing_a_password(self):
        """Dana fixes a typo in a client's name, checks the audit log shows
        both the mistake and the fix, then changes her own password and
        confirms it never turns up anywhere on that same page."""
        browser = self.browser
        browser.submit(
            "/register",
            name="Dana Whitfield",
            email="dana@example.com",
            password="dana-first-password",
        )

        # She adds a new client, but fat-fingers the name.
        browser.submit(
            "/clients",
            name="Riverside Bakry",
            email="hello@riversidebakery.example",
            company="Riverside Bakry",
            status="lead",
        )
        client_url = browser.url

        # She spots the typo and fixes it.
        browser.submit(
            client_url,
            name="Riverside Bakery",
            email="hello@riversidebakery.example",
            company="Riverside Bakery",
            status="lead",
            notes="",
        )

        # The audit log shows both the creation and the correction — and
        # the correction shows the actual before-and-after, not just "it
        # changed".
        browser.visit("/audit")
        self.assertTrue(browser.sees("Created"), "Adding the client should leave a Created entry.")
        self.assertTrue(browser.sees("Updated"), "Fixing the typo should leave an Updated entry.")
        self.assertTrue(
            browser.sees("Riverside Bakry") and browser.sees("Riverside Bakery"),
            "The audit log should show what the name changed from and to, not just that it changed.",
        )

        # Separately, she changes her own password.
        browser.submit(
            "/settings/password",
            current_password="dana-first-password",
            new_password="dana-second-password",
        )

        # Whatever else the audit log records about that change, her new
        # password itself must never be sitting in it.
        browser.visit("/audit")
        self.assertFalse(
            browser.sees("dana-second-password"),
            "A password must never appear in the audit log, even the account's own owner's.",
        )


if __name__ == "__main__":
    unittest.main()
