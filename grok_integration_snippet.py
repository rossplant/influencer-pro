# Add these two lines near the top of main.py (after the other imports)

from grok_helper import generate_with_grok

# Then replace the generate_talking_points function and the FAQ section
# inside the /enrich endpoint with the version below.

def generate_talking_points(product: Dict[str, Any]) -> List[str]:
    result = generate_with_grok(product)
    return result.get("talking_points", [])

# Inside the enrich() function, replace the hard-coded common_questions block with:

    grok_result = generate_with_grok(data)
    data["creator_talking_points"] = grok_result.get("talking_points", [])
    data["common_questions"] = grok_result.get("faqs", [])
