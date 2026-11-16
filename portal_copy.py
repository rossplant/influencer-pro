"""Visible seller and Creatr portal strings for v0.20.0.

This is the copy source of truth. Swap a line here and the screen follows.
Ross has not signed this draft off. Strings he still needs to confirm are
read from the environment when set, and otherwise stay as the draft wording
so they are not buried in templates.

The post-pilot Growth price is the wording Ross set for this release:
"A$39.50 a month for 3 months, then A$79". No percent-off line.
"""

from __future__ import annotations

import os

# Lines that stay in the product and still need Ross. Listed for the PR.
ROSS_SIGNOFF = (
    "Hand-picked Creatrs. Matched to your products and audience.",
    "This is your home for Plantr Collab: a great service that brings you hand-picked Creatrs, matched to your products and audience, who choose to work with you.",
    "Finish your hero products so the right Creatrs can see exactly why they'd want to get behind them.",
    "Add a few hero products and tell us what makes them special. We'll line up hand-picked Creatrs who suit what you make, and you choose who to work with.",
    "Hand-picked Creatrs, matched to your products and audience, who choose to work with you.",
    "Get your products in front of people who care about what you make.",
    "Matching is AI-assisted and reviewed by our team. Follower and engagement figures are self-reported, and reach is an estimate.",
    "We're finding Creatrs who suit your products. Finish your hero products and campaign brief, and we'll have the right people lined up for you.",
    "Tell Creatrs what you're after and who it's for. Your brief helps us line up the right people for you.",
    "The kind of person this product suits.",
    "Give accepted Creatrs a time-limited first look before the campaign opens up. You set the window.",
    "Pick a hero product, write a short brief and we'll help you find Creatrs who'll get behind it.",
    "Make it clear, make it you, and help the right Creatrs see why they'd love to work with you.",
    "Who follows you and what they come for. Self-reported, and shown to sellers as self-reported.",
    "Foundr: one of our founding Creatrs.",
    "Every plan gets you hand-picked Creatrs and a simple way to find and track partners.",
    "PILOT · first look",
    "First look: yours to say yes to until the time the seller set.",
    "A$0 until 20 Jan 2027, then the post-pilot price.",
)


def ross(name: str, default: str) -> str:
    """A placeholder Ross can replace without a code change. Never hardcode the decision in a template."""
    value = os.getenv(name, "").strip()
    return value or default


# Ross set this price for the release. Still overridable, and never written as a percent off.
POST_PILOT_GROWTH_PRICE = ross(
    "ROSS_POST_PILOT_GROWTH_PRICE",
    "A$39.50 a month for 3 months, then A$79",
)
PILOT_RENEWAL = ross(
    "ROSS_PILOT_RENEWAL",
    "[Ross to confirm whether Growth continues only if you opt in, or moves to a paid plan automatically]",
)
PILOT_NOTICE = ross(
    "ROSS_PILOT_NOTICE",
    "[Ross to confirm the notice period]",
)
SELLER_PLAN_FEATURES = ross(
    "ROSS_SELLER_PLAN_FEATURES",
    "[Ross to confirm plan features]",
)
CREATR_PLAN_FEATURES = ross(
    "ROSS_CREATR_PLAN_FEATURES",
    "[Ross to confirm plan features]",
)
FOUNDR_PERKS = ross(
    "ROSS_FOUNDR_PERKS",
    "[Ross to confirm Foundr perks]",
)
CREATR_PILOT = ross(
    "ROSS_CREATR_PILOT",
    "[Ross to confirm Creatr pilot terms and renewal, opt-in or automatic]",
)
MATCH_LIMITS = ross(
    "ROSS_MATCH_LIMITS",
    "[Ross to confirm the per-plan limits]",
)
LIVE_LINKS_AFTER_CANCEL = ross(
    "ROSS_LIVE_LINKS",
    "[Ross to confirm what happens to live links]",
)

# Brand names: Creatr, Foundr, Influencr, Membr, Curatr, Trendsettr, Debutr.
# "supplier" and "suppliers" stay ordinary English. They are not an r-ending brand name.
# Debutr is the public name of the paid seller launch add-on. No screen renders it.
DEBUTR_NAME = "Debutr"
CREATR_STRAPLINE = "Find products you genuinely rate. Cultivate their story, your way."

VALUE_STRIP = "Hand-picked Creatrs. Matched to your products and audience."
HERO_BADGE = "PILOT · first look"
COMMISSION_LINE = "Commission is set and paid by the seller, not Plantr."
DISCLOSURE = (
    "Keep it real: share your honest take after you've genuinely tried it, "
    "and always disclose with #ad plus the platform's paid-partnership label."
)
CLAIMS_LABEL = "Seller-supplied claims"
CLAIMS_NOTE = "These come from the seller. Only repeat what you've seen for yourself."

SELLER_NAV = (
    ("dashboard", "/seller", "Dashboard"),
    ("profile", "/seller/profile", "Profile"),
    ("products", "/seller/products", "Hero products"),
    ("find", "/seller/find", "Find Creatrs"),
    ("accepted", "/seller/accepted", "Accepted"),
    ("campaigns", "/seller/campaigns", "Campaigns"),
    ("billing", "/seller/billing", "Billing"),
)

CREATR_NAV = (
    ("dashboard", "/creator", "Home"),
    ("offers", "/creator/offers", "Matches"),
    ("links", "/creator/links", "Links"),
    ("profile", "/creator/profile", "Profile"),
    ("billing", "/creator/billing", "Your plan"),
)


def pilot_pill(plan_name: str, *, is_pilot: bool) -> tuple[str, str]:
    title = f"{plan_name} · Pilot" if is_pilot else plan_name
    if is_pilot:
        detail = f"A$0 until 20 Jan 2027, then {POST_PILOT_GROWTH_PRICE}. {PILOT_RENEWAL}"
    else:
        detail = "A monthly subscription. Prices in AUD, ex GST."
    return title, detail


# --- Dashboard ---

SELLER_DASHBOARD = {
    "heading": "Welcome back, {name}. Let's get your products some love.",
    "subhead": (
        "This is your home for Plantr Collab: a great service that brings you hand-picked Creatrs, "
        "matched to your products and audience, who choose to work with you. "
        "Track every partner, link and campaign in one simple place."
    ),
    "products": "{live} live · {need} need a detail or two",
    "crew": "{n} accepted",
    "campaigns": "{n}",
    "plan": "{plan}",
    "next": "Finish your hero products so the right Creatrs can see exactly why they'd want to get behind them.",
    "find": "Find your next collaborator",
    "add": "Add a hero product",
    "campaign": "Start a campaign",
}
SELLER_DASHBOARD_EMPTY = {
    "heading": "You're in. Let's find your people.",
    "body": (
        "Add a few hero products and tell us what makes them special. "
        "We'll line up hand-picked Creatrs who suit what you make, and you choose who to work with."
    ),
    "button": "Add your first hero product",
}
CREATR_DASHBOARD = {
    "heading": "Hi {name}. Ready for your next collab?",
    "subhead": CREATR_STRAPLINE,
    "matches": "See your matches",
    "links": "Get your links",
}
CREATR_DASHBOARD_EMPTY = {
    "heading": "Your first matches are on their way.",
    "body": (
        "We're hand-picking products that suit your niche. You'll see them here, with the brief, "
        "the seller-supplied claims and the seller's commission, so you can decide with all the facts."
    ),
    "button": "Finish your profile",
}

# --- Hero products ---

HERO_INTRO = (
    "Pick the products you're proudest of, the ones you'd love Creatrs to get their hands on. "
    "For each one, tell us in a line or two what it is, who it's for and what makes it special, "
    "in facts you can stand behind. Give Creatrs a clear story they can try for themselves and "
    "genuinely get behind. Start with a focused few. You don't need the whole shop."
)
PRODUCTS = {
    "heading": "Your hero products",
    "intro": HERO_INTRO,
    "count": "{n} hero products",
    "count_one": "{n} hero product",
    "details": "{n} details to add",
    "detail": "1 detail to add",
    "finish": "Finish it",
    "form": "Add a hero product",
    "edit_form": "Finish this hero product",
    "name": "Product name",
    "name_placeholder": "e.g. Amber Grain Honey Oat Granola",
    "category": "Category",
    "category_placeholder": "Choose a category",
    "price": "Price (A$)",
    "price_placeholder": "14.50",
    "url": "Product page link",
    "url_placeholder": "yourshop.com/products/…",
    "special": "What makes it special",
    "special_help": (
        "Your key selling point, in your own words. Stick to facts about the product, "
        "like what it's made of, how it's used and who it's for."
    ),
    "creatr_value": "What's in it for Creatrs",
    "creatr_help": "Commission you'll pay, samples, a great story to tell. Make it worth their while.",
    "commission": "Commission you'll pay (%)",
    "guard": (
        "Heads up: health, results, before-and-after, green or origin claims, price comparisons "
        "and customer quotes need evidence before Creatrs can see them. Keep it to product facts for now."
    ),
    "button": "Add a hero product",
    "save": "Save this hero product",
}
PRODUCTS_EMPTY = {
    "heading": "Your spotlight is waiting.",
    "body": "Add your first hero product and give Creatrs a reason to get excited about it. A focused few is all it takes to get started.",
    "button": "Add a hero product",
}

# --- Find Creatrs / Matches ---

FIND = {
    "heading": "Find your next collaborator",
    "subhead": (
        "Hand-picked Creatrs, matched to your products and audience, who choose to work with you. "
        "It's a great service that does the searching for you: no cold DMs and no endless scrolling, "
        "just the right people for what you make."
    ),
    "reach": "Get your products in front of people who care about what you make.",
    "why": "Why we matched you",
    "footnote": (
        "Matching is AI-assisted and reviewed by our team. "
        "Follower and engagement figures are self-reported, and reach is an estimate."
    ),
    "invite": "Invite to collab",
    "pass": "Not this time",
    "run": "Find Creatrs for this product",
    "need_product": "Finish a hero product first: what makes it special, what's in it for Creatrs, and the commission you'll pay.",
}
FIND_EMPTY = {
    "heading": "Your shortlist is being hand-picked.",
    "body": (
        "We're finding Creatrs who suit your products. Finish your hero products and campaign brief, "
        "and we'll have the right people lined up for you."
    ),
    "button": "Finish your campaign brief",
}
MATCHES = {
    "heading": "Your matches",
    "subhead": CREATR_STRAPLINE,
    "why": "Why it's a match",
    "footnote": "AI-assisted, reviewed by our team",
    "in": "I'm in",
    "out": "Not for me",
    "commission": "{rate}% of tracked sales, set and paid by the seller, not Plantr.",
    "commission_missing": "The seller hasn't set a commission yet. Plantr does not pay Creatrs.",
}
MATCHES_EMPTY = {
    "heading": "Fresh matches are being hand-picked.",
    "body": "Keep your categories and profile up to date so we can line up products that genuinely suit you.",
    "button": "Update my categories",
}

# --- Accepted ---

ACCEPTED = {
    "heading": "Your Creatr crew",
    "subhead": (
        "These Creatrs said yes. They chose to work with you, and they're ready to get behind your products. "
        "Share the brief and keep track of every partner here."
    ),
    "links": "View links",
}
ACCEPTED_EMPTY = {
    "heading": "No yeses yet. They're coming.",
    "body": "Once Creatrs accept your invite, they'll appear here with their links and codes ready to go. Meanwhile, invite a few more from your shortlist.",
    "button": "Find your next collaborator",
}
COLLABS = {
    "heading": "Your collabs",
    "subhead": "Products you've said yes to. Try them properly, share your honest take and track everything in one spot.",
}
COLLABS_EMPTY = {
    "heading": "Your first collab is one yes away.",
    "body": "Find a match you're genuinely excited about and tap \"I'm in\".",
    "button": "See your matches",
}

# --- Campaigns (name field exists; brief tools are not built) ---

CAMPAIGNS = {
    "heading": "Campaigns that get people talking",
    "subhead": "Tell Creatrs what you're after and who it's for. Your brief helps us line up the right people for you.",
    "name": "Campaign name",
    "button": "Start a campaign",
}
CAMPAIGNS_EMPTY = {
    "heading": "Your first campaign starts here.",
    "body": "Pick a hero product, write a short brief and we'll help you find Creatrs who'll get behind it.",
    "button": "Start a campaign",
}

# --- Profile ---

SELLER_PROFILE = {
    "heading": "Your brand, front and centre",
    "subhead": "This is what Creatrs see when they meet you. Make it clear, make it you, and help the right Creatrs see why they'd love to work with you.",
    "business": "Business name",
    "abn": "ABN",
    "category": "Categories",
    "shop": "Shop link",
    "shopify": "Shopify domain",
    "button": "Update profile",
}
SELLER_PROFILE_EMPTY = {
    "heading": "Let's introduce you.",
    "body": "A few lines about your business help Creatrs get to know you, and help us find the right ones for you.",
    "button": "Complete your profile",
}
CREATR_PROFILE = {
    "heading": "Your Creatr profile",
    "subhead": "Show sellers what you're about. The clearer your niche, the better-suited your matches.",
    "foundr": "Foundr: one of our founding Creatrs.",
    "active": "Categories you love",
    "desired": "Categories you want next",
    "button": "Update profile",
    "perks_note": FOUNDR_PERKS,
}
CREATR_PROFILE_EMPTY = {
    "heading": "Your profile is your pitch.",
    "body": "Add your niche and platforms so we can match you with products you'll genuinely want to try.",
    "button": "Complete your profile",
}

# --- Plan and billing (one screen each; both sets of lines are shown) ---

SELLER_PLAN = {
    "heading": "Your plan",
    "subhead": (
        "A simple monthly subscription. Plantr takes no fee on your sales, "
        "and any Creatr commission is set and paid by you. Prices in AUD, ex GST. GST is added on tax invoices."
    ),
    "starter": "Starter A$29",
    "growth": "Growth A$79",
    "scale": "Scale A$229",
    "features": SELLER_PLAN_FEATURES,
    "pilot_now": "Now: A$0 until 20 Jan 2027.",
    "pilot_after": "After 20 Jan 2027: {price} a month ex GST.",
    "renewal": PILOT_RENEWAL,
    "choose": "Choose a plan",
    "manage": "Manage or cancel",
}
SELLER_PLAN_EMPTY = {
    "heading": "Pick the plan that fits.",
    "body": "Every plan gets you hand-picked Creatrs and a simple way to find and track partners. Choose yours to get started.",
    "button": "Choose a plan",
}
SELLER_BILLING = {
    "heading": "Billing, sorted",
    "subhead": (
        "Prices in AUD, ex GST. GST is added and shown on tax invoices. "
        "Growth is a subscription, and any Creatr commission is separate and paid by you directly."
    ),
    "pilot": "Growth · Pilot: A$0 until 20 Jan 2027, then {price}. {renewal}",
}
BILLING_EMPTY = {
    "heading": "No invoices yet.",
    "body": "Your tax invoices will appear here once a paid plan starts.",
}
CREATR_PLAN = {
    "heading": "Your Creatr plan",
    "subhead": "Creatr plans: Membr, Curatr, Trendsettr. A flat monthly subscription. Plantr takes no cut of your commission. Prices in AUD, ex GST.",
    "membr": "Membr A$15",
    "curatr": "Curatr A$19",
    "trend": "Trendsettr A$49",
    "features": CREATR_PLAN_FEATURES,
    "foundr": "You're a Foundr, one of our founding Creatrs.",
    "pilot": CREATR_PILOT,
    "limits": MATCH_LIMITS,
}
CREATR_BILLING = {
    "heading": "Billing",
    "subhead": "Prices in AUD, ex GST. GST is added and shown on tax invoices. Billed by Stripe as a subscription, with no fees taken from your commission.",
}
CREATR_BILLING_EMPTY = {
    "heading": "No invoices yet.",
    "body": "Your tax invoices will show up here after your first payment.",
}
CREATR_PLAN_EMPTY = {
    "heading": "Activate your membership.",
    "body": "Choose your Creatr plan to start seeing products chosen with care for you.",
    "button": "See Creatr plans",
}

LINKS = {
    "heading": "Your links and codes",
    "subhead": "Your own link and code for each product, ready to drop into your posts.",
    "disclose": "Before you post: share your honest take after genuine use, add #ad and switch on the platform's paid-partnership label.",
}
LINKS_EMPTY = {
    "heading": "No links yet.",
    "body": "Say yes to a match and your link and code show up here straight away.",
    "button": "See your matches",
}

# Assumed screens. Kept here so the copy can be swapped later. Not rendered.
ASSUMED_SELLER_OFFERS = {
    "heading": "Offers Creatrs can share",
    "subhead": "Give Creatrs something their followers will be glad to hear about. Keep it clear, keep it dated and keep it honest.",
}
ASSUMED_SELLER_LINKS = {
    "heading": "Every link, every Creatr, one view",
    "subhead": "Each Creatr gets their own link and code, so you can track who's sharing what. Simple.",
}

SYSTEM = {
    "error_heading": "Something went wrong on our end.",
    "error_body": "Give it a moment and try again. Your work is still here.",
    "error_button": "Try again",
    "locked_heading": "Your account is locked for now.",
    "locked_body": "For your security, we've paused sign-in. Reset your password or contact us to unlock it.",
    "locked_button": "Reset password",
}
