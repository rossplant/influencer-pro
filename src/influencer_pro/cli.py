import argparse
import json

from influencer_pro import build_campaign_summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a simple influencer marketing campaign summary."
    )
    parser.add_argument(
        "--campaign",
        default="summer-launch",
        help="Campaign name or theme.",
    )
    parser.add_argument(
        "--influencers",
        nargs="*",
        default=["@creatorone", "@creatortwo"],
        help="List of creator handles for the campaign.",
    )
    args = parser.parse_args(argv)

    summary = build_campaign_summary(args.influencers, args.campaign)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
