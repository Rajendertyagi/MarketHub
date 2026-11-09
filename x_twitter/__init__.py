"""X/Twitter feature package (V1, twitter-cli adapter boundary).

MarketHub → XTwitterService → twitter-cli. No X/private-API assumptions
leak into MCP, React, or core architecture. The CLI layer is consumed
ONLY by XTwitterService.
"""
