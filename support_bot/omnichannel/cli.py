from __future__ import annotations

import argparse

from support_bot.omnichannel.auth import TokenSigner
from support_bot.omnichannel.settings import OmnichannelSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Headless support module administration"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    token = subparsers.add_parser(
        "issue-token",
        help="Issue a signed integration token",
    )
    token.add_argument(
        "--role",
        required=True,
        choices=["operator", "admin", "identity"],
    )
    token.add_argument("--subject", required=True)
    token.add_argument("--ttl", type=int, default=3600)
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = OmnichannelSettings.from_env()
    if args.command == "issue-token":
        print(
            TokenSigner(settings.auth_secret).issue(
                subject=args.subject,
                role=args.role,
                ttl_seconds=args.ttl,
            )
        )


if __name__ == "__main__":
    main()
