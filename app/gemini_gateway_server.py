from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    # Container deployments require an external bind; local operators can set HOST=127.0.0.1.
    host = os.getenv("HOST", "0.0.0.0")  # nosec B104
    uvicorn.run(
        "app.gemini_gateway_app:app",
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
