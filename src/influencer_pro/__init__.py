__all__ = ["build_campaign_summary"]


def build_campaign_summary(influencers, campaign_name):
    """Build a simple summary for a marketing campaign."""
    return {
        "campaign": campaign_name,
        "influencers": list(influencers),
        "creator_count": len(influencers),
        "status": "planned",
    }
