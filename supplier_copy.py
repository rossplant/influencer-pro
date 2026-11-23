"""Supplier-facing sentences. Creatr tiers are explained here only as a glossary.

Nothing in this module tells a supplier they have a tier to climb.
"""

HOME_LINE = "Carefully selected Creatrs, matched to your products."
HOW_STEP = "Track sales and pay Creatrs."
MARKETING_TILE = "Headlinr means 40+ confirmed sales in the last 3 months."
BILLING_TIP = (
    "Add a card at sign-up. There is no charge before 20 January 2027. "
    "We send reminders, and you can cancel any time."
)
PRODUCT_CAP = "No product cap in the pilot."
TOS_LEAD = "All users must be 18 or over. You must be 18 or over and authorised to apply for this business."
LEADERBOARD_HELP = "Ranking boards are for Creatrs. Suppliers are not listed by tier."
SHORTLIST_INTRO = "Each Creatr's tier is a note on their confirmed sales, to help you choose."
FILTER_LABEL = "Trendsettr or Headlinr"
ATTRIBUTION_NOTE = "These labels are about the Creatr, not about you."
REVIEW_NOTE = "If the review succeeds, that month does not count toward losing Pays on time or Top seller."
GLOSSARY_LEAD = "You do not have a tier, and there is nothing to climb."
GLOSSARY = (
    "Contributr: a Creatr who has just joined.",
    "Influencr: a Creatr with 5+ confirmed sales in the last 3 months, or a pilot invite.",
    "Trendsettr: a Creatr with 15+ confirmed sales in the last 3 months.",
    "Headlinr: a Creatr with 40+ confirmed sales in the last 3 months.",
    "Counts are refund-adjusted, over a rolling 3 months, from 20 November 2026.",
)
BADGE_PAGE_TITLE = "Notes Creatrs may see"
PAYS_ON_TIME_NOTE = "Pays on time: commission is settled on time, and samples are sent on time."
TOP_SELLER_NOTE = (
    "Top seller: 20 or more confirmed sales through 5 or more Creatrs in the last 3 months, and Pays on time. "
    "It is a note only. It does not change match order and it is not bought."
)
BADGE_ON = "{name} is on your profile. Creatrs may see this note."
BADGE_OFF = "{name} is no longer on your profile."

# Phrases that must not appear in supplier screens, emails, or help.
BANNED = (
    "unlock Trendsettr",
    "unlock Influencr",
    "level up",
    "you unlocked",
    "more sales to unlock",
    "Start at Influencr",
    "supplier tier",
    "How you move up",
)


def badge_notice(name: str, present: bool) -> str:
    template = BADGE_ON if present else BADGE_OFF
    return template.format(name=name)


def visible_strings() -> list[str]:
    values = [
        HOME_LINE,
        HOW_STEP,
        MARKETING_TILE,
        BILLING_TIP,
        PRODUCT_CAP,
        TOS_LEAD,
        LEADERBOARD_HELP,
        SHORTLIST_INTRO,
        FILTER_LABEL,
        ATTRIBUTION_NOTE,
        REVIEW_NOTE,
        GLOSSARY_LEAD,
        BADGE_PAGE_TITLE,
        PAYS_ON_TIME_NOTE,
        TOP_SELLER_NOTE,
        badge_notice("Pays on time", True),
        badge_notice("Top seller", False),
        *GLOSSARY,
    ]
    return values
