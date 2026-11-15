"""Shared category list for Plantr Collab.

Top-level slugs only, plus ``other``. A product subcategory is free text on the
product and is not a second vertical. Nothing in this list is a starting niche.
"""

TOP_LEVEL_CATEGORIES = (
    ("apparel", "Apparel"),
    ("beauty", "Beauty"),
    ("electronics", "Electronics"),
    ("food-and-drink", "Food and drink"),
    ("health", "Health"),
    ("home", "Home"),
    ("kids", "Kids"),
    ("sports", "Sports"),
    ("other", "Other"),
)


def slugs() -> set[str]:
    return {slug for slug, _name in TOP_LEVEL_CATEGORIES}
