"""
Tools package init — re-exports registration functions for convenience.
"""

from mcp_server.tools.system import register_system_tools
from mcp_server.tools.events import register_event_tools
from mcp_server.tools.consumers import register_consumer_tools
from mcp_server.tools.replay import register_replay_tools
from mcp_server.tools.sources import register_source_tools
from mcp_server.tools.background import register_background_tools
from mcp_server.tools.dev import register_dev_tools
from mcp_server.tools.alerts import register_alert_tools
from mcp_server.tools.market import register_market_tools

from mcp_server.tools.market_intel_tools import register_market_intel_tools

from mcp_server.tools.market_alerts import register_market_alert_tools

from mcp_server.tools.options_analytics_tools import register_options_analytics_tools

__all__ = [
    "register_system_tools",
    "register_event_tools",
    "register_consumer_tools",
    "register_replay_tools",
    "register_source_tools",
    "register_background_tools",
    "register_dev_tools",
    "register_alert_tools",
    "register_options_analytics_tools",
]
