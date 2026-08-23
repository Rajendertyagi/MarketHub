"""
MarketHub broker-integration package (Phase A boundary marker).

Future owner of provider integrations, per the migration plan:

    brokers/base.py        shared adapter contracts/helpers
    brokers/registry.py    static provider registry (same pattern as
                           sources/registry.py — explicit, no discovery)
    brokers/<provider>/    auth/session, REST client, WebSocket feed adapter

Deliberately EMPTY in Phase A: no registrations, no imports, no provider
code. Broker adapters will implement the existing sources.EventSource
protocol and plug into the existing SourceManager lifecycle; they will
depend on market/ and core/, never the reverse.
"""
