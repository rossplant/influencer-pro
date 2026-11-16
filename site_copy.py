"""Landing and privacy copy.

Landing copy is approved. Marketing can still edit these strings.
The privacy page stays marked draft for legal review.
"""

from __future__ import annotations

import html

APPROVED = True

CORE_HEADLINE = "Sell more. Earn more. Matched with care."
CORE_BODY = (
    "An invite-only marketplace connecting selected Creatrs with underserved suppliers. "
    "Creatrs earn commission on the sales they drive; suppliers sell through Creatrs who fit "
    "their products — matched on category, audience, performance and supplier fit, reviewed by our team."
)

CREATOR_PAGE = {
    "path": "/founding-curators",
    "role": "creator",
    "cohort": "founding_curators",
    "headline": "Earn from products your audience will actually buy.",
    "subhead": "You're invited to apply. Earn commission on the tracked sales you drive.",
    "cta": "Request your invite",
    "points": (
        "Invite-only. Foundrs are numbered and the cohort is capped.",
        "Curated suppliers in your niche, chosen because they are underserved on TikTok, Instagram and YouTube.",
        "A time-boxed first look at new suppliers: Curatr for 48 hours, Trendsettr for 7 days, where the seller opted in.",
        "Tailored offers and bundle ideas.",
        "Your own tracking links and codes. The supplier sets the commission and pays it only on tracked sales.",
        "A title, badge and profile. Your Foundr number is shown once you are approved.",
        "Foundrs have no charge for the pilot, from 20 November 2026 until 20 January 2027, then Curatr is A$9 a month ex GST for 12 months, then A$19 a month ex GST.",
        "Membr is A$15 a month ex GST or A$150 a year ex GST. Curatr is A$19 ex GST or A$190 ex GST. Trendsettr is A$49 ex GST or A$490 ex GST. Annual prices are two months off.",
        "Curatr only: your next monthly Curatr fee is waived when you drive 5 or more attributed sales in a month. It does not apply to Membr or Trendsettr.",
    ),
}

SUPPLIER_PAGE = {
    "path": "/pilot-sellers",
    "role": "seller",
    "cohort": "seller_pilot",
    "headline": "Sell more through the right Creatrs.",
    "subhead": "Great products deserve the right voices.",
    "cta": "Apply for a pilot place",
    "points": (
        "We select suppliers that are underserved on TikTok, Instagram and YouTube.",
        "Curated Creatr shortlists, reviewed by our team.",
        "Sales through your Creatrs' tracking links and codes are reported to you.",
        "You set the commission and pay it only on tracked sales, settled directly or via TikTok Shop or Shopify Collabs.",
        "Brief help for the Creatrs you accept.",
        "10 pilot places. Growth has no charge for the pilot, from 20 November 2026 until 20 January 2027, then A$39.50 a month ex GST for 3 months, then A$79 a month ex GST.",
        "Starter is A$29 a month ex GST or A$290 a year ex GST. Growth is A$79 ex GST or A$790 ex GST. Scale is A$229 ex GST or A$2,290 ex GST. Annual prices are two months off.",
    ),
}

SHOW_TESTIMONIALS = False

PRIVACY = {
    "draft": True,
    "title": "Privacy policy",
    "notice": "Draft for legal review. This is not the final policy.",
    "body": (
        "Plantr Collab stores the details you submit on an application or invite, "
        "including your name, email, categories, and the site or social handle you give us. "
        "If you allow marketing cookies, we may share a hashed email and hashed phone, "
        "along with your IP address and browser user agent, with advertising platforms so we "
        "can measure applications and paid subscriptions. Those platforms are TikTok, Meta, "
        "Google, Pinterest and LinkedIn. Marketing tags stay off until they are configured "
        "and you consent. We do not send events for anyone who says they are under 18. "
        "Payment details are collected by Stripe. Plantr does not pay advertising income."
    ),
}


def page(kind: str) -> dict:
    return CREATOR_PAGE if kind == "creator" else SUPPLIER_PAGE


def landing_html(kind: str, *, token: str = "", thanks: str = "", event_id: str = "") -> str:
    spec = page(kind)
    points = "".join(f"<li>{html.escape(point)}</li>" for point in spec["points"])
    testimonials = ""
    if SHOW_TESTIMONIALS:
        testimonials = "<section><h2>Stories</h2></section>"
    thanks_block = f"<p class='thanks'>{html.escape(thanks)}</p>" if thanks else ""
    import billing_config
    import legal

    operator = billing_config.operator_legal_name()
    if spec["role"] == "creator":
        panel = legal.CREATOR_PANEL
        role_fields = legal.checkbox("age_confirmed", legal.CREATOR_AGE, required=True)
        paid = legal.checkbox("paid_service_confirmed", legal.CREATOR_APPLY, required=True)
    else:
        panel = legal.SELLER_PANEL
        role_fields = (
            "<label>Business name <input name='business_name' autocomplete='organization' required></label>"
            "<label>ABN (optional, 11 digits) <input name='abn' inputmode='numeric' pattern='[0-9 ]{11,14}'></label>"
            + legal.checkbox("authority_confirmed", legal.SELLER_AUTHORITY, required=True)
        )
        paid = legal.checkbox("paid_service_confirmed", legal.SELLER_APPLY, required=True)
    email_box = legal.checkbox("email_consent", legal.EMAIL_CONSENT, required=False)
    if operator:
        operator_line = f"<p class='operator'>Plantr Collab is operated by {html.escape(operator)}.</p>"
        form = f"""<form method="post" action="{html.escape(spec['path'])}/apply" data-role="{html.escape(spec['role'])}">
      <input type="hidden" name="csrf_token" value="{html.escape(token)}">
      <input type="hidden" name="event_id" value="{html.escape(event_id)}">
      <input type="hidden" name="role" value="{html.escape(spec['role'])}">
      <label>Name <input name="name" autocomplete="name" required></label>
      <label>Email <input name="email" type="email" autocomplete="email" required></label>
      <label>Categories <input name="categories" placeholder="home, beauty"></label>
      <label>Social handle or website <input name="contact" autocomplete="url"></label>
      {role_fields}
      <p class='panel'>{html.escape(panel)}</p>
      {paid}
      {email_box}
      <button type="submit">{html.escape(spec['cta'])}</button>
    </form>"""
    else:
        operator_line = ""
        form = f"<p class='panel'>{html.escape(panel)}</p><p class='soon'>{html.escape(legal.OPENING_SOON)}</p>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(spec['headline'])}</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; color: #1c1917; background: #fafaf9; }}
    main {{ max-width: 40rem; margin: 0 auto; padding: 24px 16px 48px; }}
    h1 {{ font-size: 1.8rem; line-height: 1.2; }}
    .core {{ color: #44403c; }}
    form {{ display: grid; gap: 10px; background: white; padding: 16px; border-radius: 12px; }}
    label {{ display: grid; gap: 4px; font-size: 0.95rem; }}
    input, textarea {{ font: inherit; padding: 10px; border: 1px solid #d6d3d1; border-radius: 8px; }}
    button {{ font: inherit; padding: 12px; border: 0; border-radius: 8px; background: #1c1917; color: white; }}
    .check {{ display: flex; gap: 8px; align-items: center; }}
    a {{ color: #1c1917; }}
  </style>
</head>
<body>
  <main>
    <p class="core"><strong>{html.escape(CORE_HEADLINE)}</strong> {html.escape(CORE_BODY)}</p>
    <h1>{html.escape(spec['headline'])}</h1>
    <p>{html.escape(spec['subhead'])}</p>
    <ul>{points}</ul>
    {testimonials}
    {thanks_block}
    {form}
    {operator_line}
    <p><a href="/privacy">Privacy</a></p>
  </main>
  <script>
    (function () {{
      var form = document.querySelector("form");
      if (!form || form.dataset.started) return;
      var sent = false;
      form.addEventListener("focusin", function () {{
        if (sent) return;
        sent = true;
        fetch("/track/apply-start", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{role: form.dataset.role, event_id: form.querySelector("[name=event_id]").value}})
        }});
      }});
    }})();
  </script>
</body>
</html>"""


def privacy_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(PRIVACY['title'])}</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; color: #1c1917; background: #fafaf9; }}
    main {{ max-width: 40rem; margin: 0 auto; padding: 24px 16px 48px; }}
    .draft {{ background: #fef3c7; padding: 12px; border-radius: 8px; }}
  </style>
</head>
<body>
  <main>
    <p class="draft">{html.escape(PRIVACY['notice'])}</p>
    <h1>{html.escape(PRIVACY['title'])}</h1>
    <p>{html.escape(PRIVACY['body'])}</p>
  </main>
</body>
</html>"""


def consent_banner() -> str:
    return (
        "<div class='consent'>"
        "<p>We use essential cookies to run this site. With your permission, we also use marketing cookies "
        "and share limited data (including hashed email and phone, and ad click IDs) with Meta and TikTok "
        "to measure our ads.</p>"
        "<button type='button' id='consent-yes'>Allow marketing cookies</button> "
        "<button type='button' id='consent-no'>Essential only</button> "
        "Cookie and Tracking Notice"
        "</div>"
        "<script>"
        "function plantrConsent(allowed){"
        "document.cookie='plantr_consent='+(allowed?'marketing':'denied')+';path=/;max-age=7776000;samesite=lax';"
        "fetch('/track/consent',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({consent:allowed,query:location.search.slice(1)})});"
        "}"
        "document.getElementById('consent-yes').onclick=function(){plantrConsent(true)};"
        "document.getElementById('consent-no').onclick=function(){plantrConsent(false)};"
        "</script>"
    )
