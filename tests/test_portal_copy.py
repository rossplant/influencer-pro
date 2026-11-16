"""Owner copy locks: Creatr strapline, Debutr, and ordinary 'supplier'."""

import portal
import portal_copy


STRAPLINE = "Find products you genuinely rate. Cultivate their story, your way."


def test_creatr_strapline_is_the_dashboard_and_offers_subhead():
    assert portal_copy.CREATR_STRAPLINE == STRAPLINE
    assert portal_copy.CREATR_DASHBOARD["subhead"] == STRAPLINE
    assert portal_copy.MATCHES["subhead"] == STRAPLINE
    dashboard = portal._creator_body("dashboard", {"name": "Ada"}, "tok")
    offers = portal._creator_body("offers", {"name": "Ada"}, "tok", offers=[])
    assert STRAPLINE in dashboard
    assert STRAPLINE in offers


def test_launch_addon_name_is_debutr_and_retired_names_are_not_visible():
    assert portal_copy.DEBUTR_NAME == "Debutr"
    visible = []
    for name in dir(portal_copy):
        if name.startswith("_"):
            continue
        visible.append(getattr(portal_copy, name))
    blob = repr(visible)
    assert "Debutr" in blob
    for retired in ("Boostr", "Seedr", "Supplir"):
        assert retired not in blob
    # The add-on is named for later copy. This release does not render a screen for it.
    assert portal_copy.DEBUTR_NAME not in portal._creator_body("dashboard", {"name": "Ada"}, "tok")


def test_supplier_keeps_ordinary_english_spelling():
    import site_copy

    assert "suppliers" in site_copy.CORE_BODY
    assert "Curated suppliers" in site_copy.CREATOR_PAGE["points"][1]
    assert "Supplir" not in site_copy.CORE_BODY
    assert "suppli" + "r" not in site_copy.CORE_BODY.lower()
