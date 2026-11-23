"""Shared chrome for server-rendered Plantr Collab pages."""

from __future__ import annotations

import html

STYLESHEET = "/assets/plantr.css?v=0.26.0"

LOGO = """<a class="logo" href="/"><svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="pmk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C2127F"/><stop offset=".7" stop-color="#FF3D8B"/><stop offset="1" stop-color="#5B2EFF"/></linearGradient></defs><circle cx="12" cy="16" r="9" fill="url(#pmk)"/><circle cx="20" cy="16" r="9" fill="none" stroke="#0D0B18" stroke-width="2.4"/></svg><span>Plantr <i>Collab</i></span></a>"""


def document(title: str, body: str, *, nav: str = "", logout: str = "") -> str:
    menu = f'<button class="nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav">Menu</button><nav id="site-nav" class="site-nav">{nav}{logout}</nav>' if nav or logout else ""
    banner = ""
    try:
        import uat_mode

        if uat_mode.enabled():
            banner = "<p class='banner' role='status'>Test environment</p>"
    except Exception:
        banner = ""
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{STYLESHEET}">
</head><body>
<a class="skip" href="#content">Skip to content</a>
<header class="top">{LOGO}{menu}</header>
<main id="content">{banner}{body}</main>
<footer class="site-footer">Plantr Collab. No cut of your sales.</footer>
<script>
document.querySelectorAll(".nav-toggle").forEach((button) => {{
  button.addEventListener("click", () => {{
    const nav = document.getElementById(button.getAttribute("aria-controls"));
    const open = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", open ? "true" : "false");
    if (nav) nav.classList.toggle("open", open);
  }});
}});
document.querySelectorAll("[data-show-password]").forEach((button) => {{
  button.addEventListener("click", () => {{
    const input = document.getElementById(button.getAttribute("data-show-password"));
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.textContent = show ? "Hide password" : "Show password";
  }});
}});
document.querySelectorAll("button.copy").forEach((button) => {{
  button.addEventListener("click", async () => {{
    const value = button.getAttribute("data-copy") || "";
    try {{
      await navigator.clipboard.writeText(value);
      button.textContent = "Copied";
    }} catch (error) {{
      button.textContent = "Copy failed";
    }}
  }});
}});
</script>
</body></html>"""
