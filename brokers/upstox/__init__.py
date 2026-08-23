"""
Upstox broker adapter package (Phase D1: protocol foundation only).

Current scope:
    upstox.feed_protocol   V3 FeedResponse decoder + presence-exact
                           extraction helpers (P-ZERO policy)
    upstox.proto           vendored official schema + generated bindings

Later phases add: auth.py, rest.py, feed.py (WebSocket adapter),
limits.py. Broker adapters depend on market/ and core/ — never the
reverse. Raw market ticks never pass through core.events.publish_event().
"""
