"""Device types the agent finds templates for, and what differs between them.

Everything else - the official-site and internet search, the homography choice
against the photo, the store - is the same for every device type.

    switch  rack access switches: a 1U panel ~7-14x wider than tall, many ports
    router  rack routers / gateways (Cisco ISR/ASR, Juniper MX/SRX, MikroTik CCR...):
            1U or 2U, so ~3.5-14x wider than tall, few ports (often 2-12), plus
            module slots; kept in their own store so a router never stands in for a
            switch (or the reverse) in a "similar model" correction
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Device:
    name: str
    noun: str                       # word added to searches: '"<model>" router'
    sitemap_hint: str               # sitemap URLs worth reading first
    aspect: tuple[float, float]     # front panel width / height accepted by find_face
    aspect_target: float            # typical panel aspect, for the front-view score
    typical_ports: int              # ports a front panel usually shows (count unknown)
    store_dir: Path


DEVICES = {
    "switch": Device("switch", "switch", r"product|support|switch", (7, 14), 10, 8,
                     Path(os.environ.get("TEMPLATE_STORE_DIR", _AGENT_DIR / "template_store"))),
    "router": Device("router", "router", r"product|support|router|gateway|firewall", (3.5, 14), 8, 4,
                     Path(os.environ.get("ROUTER_TEMPLATE_STORE_DIR", _AGENT_DIR / "router_template_store"))),
    # appliances (appliance_mapper): 1U-2U rack units and desktop boxes, each in its own store
    "firewall": Device("firewall", "firewall", r"product|support|firewall|security|appliance", (3, 14), 8, 8,
                       Path(os.environ.get("FIREWALL_TEMPLATE_STORE_DIR", _AGENT_DIR / "firewall_template_store"))),
    "load_balancer": Device("load_balancer", "load balancer", r"product|support|adc|load|balanc|platform|appliance",
                            (4, 14), 9, 8,
                            Path(os.environ.get("LOAD_BALANCER_TEMPLATE_STORE_DIR",
                                                _AGENT_DIR / "load_balancer_template_store"))),
    "gateway": Device("gateway", "gateway", r"product|support|gateway|sbc|voice|voip|media", (2.5, 14), 7, 8,
                      Path(os.environ.get("GATEWAY_TEMPLATE_STORE_DIR", _AGENT_DIR / "gateway_template_store"))),
}


def device(name: "str | Device | None" = "switch") -> Device:
    """The profile for a device type name ('switch', 'router'); a Device passes through."""
    if isinstance(name, Device):
        return name
    try:
        return DEVICES[(name or "switch").lower()]
    except KeyError:
        raise ValueError(f"unknown device type {name!r}; one of {', '.join(DEVICES)}") from None
