"""Browser journey probe: login → add to cart → reach checkout on saucedemo.com.

One journey per run, standard demo credentials published by SauceLabs for exactly this use.
Each step is timed separately so a slow step shows up as its own series, not just a slower
total.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.probe

STEP_TIMEOUT_MS = 15_000


def test_saucedemo_checkout(journey_target, probe_recorder, page: Page):
    if journey_target.journey != "saucedemo_checkout":
        pytest.skip(f"journey {journey_target.journey} not implemented by this test")
    expect.set_options(timeout=STEP_TIMEOUT_MS)

    with probe_recorder.step("load_login"):
        page.goto(journey_target.url, timeout=STEP_TIMEOUT_MS)
        expect(page.locator("#login-button")).to_be_visible()

    with probe_recorder.step("login"):
        page.fill("#user-name", "standard_user")
        page.fill("#password", "secret_sauce")
        page.click("#login-button")
        expect(page.locator(".inventory_list")).to_be_visible()

    with probe_recorder.step("add_to_cart"):
        page.click("#add-to-cart-sauce-labs-backpack")
        expect(page.locator(".shopping_cart_badge")).to_have_text("1")

    with probe_recorder.step("reach_checkout"):
        page.click(".shopping_cart_link")
        expect(page.locator("#checkout")).to_be_visible()
        page.click("#checkout")
        expect(page.locator("#first-name")).to_be_visible()
