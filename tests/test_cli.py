from influencer_pro import build_campaign_summary


def test_create_summary():
    summary = build_campaign_summary(["@creatorone", "@creatortwo"], "summer-launch")

    assert summary["campaign"] == "summer-launch"
    assert summary["creator_count"] == 2
    assert summary["status"] == "planned"
