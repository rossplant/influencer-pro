"""Six live benefits. Each line claims only what the product already does."""

USPS = (
    ("Your site, your customer", "No cut of your sales. The supplier keeps the customer and pays the Creatr's commission."),
    ("Carefully selected, both ways", "Suppliers and Creatrs are carefully selected to match each other."),
    ("The apps they already use", "Creatrs can post on the apps their audience already uses, with the supplier's link or code."),
    ("A sample, a due date, a labelled post", "A sample has a due date and a labelled post. The address stays private until the supplier approves, then it is removed after delivery."),
    ("Listing Studio", "Listing Studio drafts positioning and scenes a Creatr can post, after the supplier approves them."),
    ("Confirmed sales", "Confirmed sales, Pays on time, and earned tiers (Contributr, Influencr, Trendsettr, Headlinr) are notes, not a plan you buy."),
)

def block() -> str:
    items = "".join(f"<li><strong>{title}.</strong> {body}</li>" for title, body in USPS)
    return f"<section class='card'><h2>What you can do</h2><ul>{items}</ul></section>"
