from __future__ import annotations

import uvicorn

from support_bot.omnichannel.api import create_app


app = create_app()


def main() -> None:
    uvicorn.run(
        "support_bot.omnichannel.api_main:app",
        host="0.0.0.0",
        port=8080,
    )


if __name__ == "__main__":
    main()
