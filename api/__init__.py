"""MarketHub HTTP API package.

Owns Starlette Route builders only. Routes receive every dependency
(brokers, services) as constructor/argument injection from the application
composition root — this package never creates services, never serializes
domain models, and never reads app.state.
"""
