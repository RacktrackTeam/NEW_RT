"""Every test runs with outside calls off: the port agents' web search and the specifications' online lookup (hard rule 2)."""
import os

os.environ["SWITCH_MAPPER_WEB_SEARCH"] = "0"
os.environ["RACKTRACK_ONLINE_LOOKUPS"] = "0"
