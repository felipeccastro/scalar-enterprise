"""Journey: taking a prospective client from a cold lead to a signed,
active account — the day-to-day reason this app exists.
"""

from __future__ import annotations

import unittest

from _harness import Journey


class ClientRelationshipJourney(Journey):

    def test_a_team_can_track_a_deal_from_lead_to_signed_client(self):
        """Priya adds a prospect, gives someone a task to chase it, wins
        the deal, notes it down, archives the account once it's wound
        down, and — realizing that was too hasty — deletes and then
        restores both the client and the task, none of it permanent."""
        browser = self.browser
        browser.submit(
            "/register",
            name="Priya Raman",
            email="priya@example.com",
            password="a-strong-password",
        )

        # A new prospect comes in. Creating one lands you straight on its
        # own page — same as it would for a person clicking through — so
        # that's captured before navigating anywhere else.
        browser.submit(
            "/clients",
            name="Riverside Bakery",
            email="hello@riversidebakery.example",
            company="Riverside Bakery",
            status="lead",
            notes="Met at the farmers' market expo.",
        )
        client_url = browser.url
        self.assertIn("/clients/", client_url)
        client_id = client_url.rsplit("/", 1)[-1]

        browser.visit("/clients")
        self.assertTrue(browser.sees("Riverside Bakery"))
        self.assertTrue(browser.sees("Lead"), "A new client should show up as a Lead.")

        # She sets a task to chase the deal, filed directly against this
        # client — same as with the client above, its own page is captured
        # right off the create redirect.
        browser.submit(
            "/tasks",
            title="Send Riverside Bakery a proposal",
            description="They want pricing for weekly deliveries.",
            status="todo",
            client_id=client_id,
        )
        task_url = browser.url
        self.assertIn("/tasks/", task_url)

        browser.visit(client_url)
        self.assertTrue(
            browser.sees("Send Riverside Bakery a proposal"),
            "A task filed against this client should show up on their page.",
        )

        # The deal closes: she updates the status and leaves a note for the
        # rest of the team.
        browser.submit(
            client_url,
            name="Riverside Bakery",
            email="hello@riversidebakery.example",
            company="Riverside Bakery",
            status="active",
            notes="Met at the farmers' market expo.",
        )
        # Checked from the list, not the detail page: the detail page's own
        # status field is a dropdown listing every possible status as an
        # option regardless of which is picked, so "Active" would appear
        # there either way — the list's badge only ever shows the one
        # that's actually current.
        browser.visit("/clients")
        self.assertTrue(browser.sees("Active"), "A won deal should show as Active.")

        browser.submit(
            "/comments",
            subject_type="client",
            subject_id=client_id,
            body="Signed! First delivery is Monday.",
        )
        browser.visit(client_url)
        self.assertTrue(
            browser.sees("Signed! First delivery is Monday."),
            "A comment left on the client should show up on their page.",
        )

        # Eventually the relationship winds down and she archives the
        # account — it disappears from the working list, but the record
        # itself still exists if anyone needs to look it up again.
        browser.submit(client_url + "/archive")
        browser.visit("/clients")
        self.assertFalse(
            browser.sees("Riverside Bakery"),
            "An archived client shouldn't clutter the active client list.",
        )
        browser.visit(client_url)
        self.assertTrue(
            browser.sees("Riverside Bakery"),
            "Archiving a client shouldn't delete their record.",
        )

        # She goes further than she meant to and deletes the client outright.
        browser.submit(client_url + "/delete")
        browser.visit("/clients?deleted=1")
        self.assertTrue(
            browser.sees("Riverside Bakery"),
            "A deleted client should show up in the deleted clients list.",
        )

        # But a delete here is never permanent — she restores it from that
        # same list, one click, no need to open the record first.
        browser.submit(client_url + "/restore")
        browser.visit("/clients?deleted=1")
        self.assertFalse(
            browser.sees("Riverside Bakery"),
            "A restored client shouldn't still show up as deleted.",
        )
        # Still archived from earlier, though — restoring undoes the
        # delete, not the separate, one-way archive decision.
        browser.visit("/clients")
        self.assertFalse(
            browser.sees("Riverside Bakery"),
            "Restoring a client doesn't also un-archive them.",
        )

        # The task she filed earlier gets the same reversible treatment.
        browser.submit(task_url + "/delete")
        browser.visit("/tasks?deleted=1")
        self.assertTrue(
            browser.sees("Send Riverside Bakery a proposal"),
            "A deleted task should show up in the deleted tasks list.",
        )
        browser.submit(task_url + "/restore")
        browser.visit(task_url)
        self.assertTrue(
            browser.sees("Send Riverside Bakery a proposal"),
            "A restored task should still be there, editable as normal.",
        )


if __name__ == "__main__":
    unittest.main()
