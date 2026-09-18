"""Journey: a brand new team's very first few minutes in the app.

This is the moment that decides whether someone keeps using the product at
all — sign up, land somewhere that already has *something* on the screen,
fail to get back in on a guess, then succeed with the real password.
"""

from __future__ import annotations

import unittest

from _harness import Journey


class GettingStartedJourney(Journey):

    def test_a_new_owner_can_sign_up_and_come_back_later(self):
        """Maria signs up her team, sees the app isn't an empty room, fails
        to get back in on a guessed password, then succeeds with the real
        one."""
        browser = self.browser

        # Before anyone has an account, the app sends you straight to sign up.
        browser.visit("/")
        self.assertTrue(browser.sees("Register") or browser.sees("register"))

        # Maria signs up.
        browser.submit(
            "/register",
            name="Maria Chen",
            email="maria@example.com",
            password="a-strong-password",
        )
        self.assertTrue(
            browser.sees("Maria") or browser.sees("Dashboard"),
            "Signing up should land Maria in the app, not back on a form.",
        )

        # A brand new team isn't a blank screen — there's sample data to
        # get a feel for the product with.
        browser.visit("/clients")
        self.assertTrue(
            browser.sees("Acme Corp"),
            "A fresh account should already have a sample client to look at.",
        )

        # She's done for the day.
        browser.submit("/logout")

        # A signed-out visitor can't see the client list.
        browser.visit("/clients")
        self.assertFalse(browser.sees("Acme Corp"))

        # The next day, she mistypes her password.
        browser.submit("/login", email="maria@example.com", password="a-total-guess")
        self.assertFalse(
            browser.sees("Dashboard"),
            "A wrong password should never land someone inside the app.",
        )
        self.assertTrue(
            browser.sees("Incorrect") or browser.sees("incorrect"),
            "Someone who mistypes their password should be told, not left guessing.",
        )

        # She tries again with the real one, and gets back to exactly where
        # she left off.
        browser.submit("/login", email="maria@example.com", password="a-strong-password")
        browser.visit("/clients")
        self.assertTrue(
            browser.sees("Acme Corp"),
            "Logging back in should show the same team data as before.",
        )


if __name__ == "__main__":
    unittest.main()
