"""
Scraper registry. Maps vendor names to their scraper classes.
"""
from .aruba import ArubaScraper
from .arista import AristaScraper
from .base import BaseScraper, SpecRecord, upsert_records
from .cisco import CiscoScraper
from .dell import DellScraper
from .edgecore import EdgecoreScraper
from .extreme import ExtremeScraper
from .fortinet import FortinetScraper
from .h3c import H3CScraper
from .huawei import HuaweiScraper
from .juniper import JuniperScraper
from .lenovo import LenovoScraper
from .mikrotik import MikroTikScraper
from .netgear import NetgearScraper
from .nvidia import NvidiaScraper
from .ruijie import RuijieScraper
from .tplink import TPLinkScraper
from .ubiquiti import UbiquitiScraper
from .zyxel import ZyxelScraper


REGISTRY: dict[str, type[BaseScraper]] = {
    "ubiquiti": UbiquitiScraper,
    "mikrotik": MikroTikScraper,
    "tplink":   TPLinkScraper,
    "netgear":  NetgearScraper,
    "arista":   AristaScraper,
    "dell":     DellScraper,
    "nvidia":   NvidiaScraper,
    "aruba":    ArubaScraper,
    "juniper":  JuniperScraper,
    "cisco":    CiscoScraper,
    "extreme":  ExtremeScraper,
    "fortinet": FortinetScraper,
    "huawei":   HuaweiScraper,
    "h3c":      H3CScraper,
    "ruijie":   RuijieScraper,
    "edgecore": EdgecoreScraper,
    "zyxel":    ZyxelScraper,
    "lenovo":   LenovoScraper,
}

__all__ = ["REGISTRY", "BaseScraper", "SpecRecord", "upsert_records"]
