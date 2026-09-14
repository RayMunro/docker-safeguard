"""Read/write unRAID's own Docker template XML
(/boot/config/plugins/dockerMan/templates-user/my-<Name>.xml). Capturing it
gives near-perfect UI fidelity on restore (icon, category, WebUI link,
friendly volume/variable names) as a complement to the low-level docker
inspect data that actually recreates the container."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from .config import TEMPLATES_USER_DIR

log = logging.getLogger("docker_safeguard.template_xml")


def template_path(container_name: str):
    return TEMPLATES_USER_DIR / f"my-{container_name}.xml"


def read_template(container_name: str) -> str | None:
    path = template_path(container_name)
    try:
        if path.exists():
            return path.read_text()
    except OSError as exc:
        log.warning("could not read template for %s: %s", container_name, exc)
    return None


def write_template(container_name: str, xml_text: str) -> None:
    path = template_path(container_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml_text)


@dataclass
class TemplateInfo:
    icon: str | None = None
    webui: str | None = None
    category: str | None = None
    overview: str | None = None
    configs: list[dict[str, Any]] | None = None


def parse_template(xml_text: str) -> TemplateInfo:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("bad template xml: %s", exc)
        return TemplateInfo()

    def text(tag: str) -> str | None:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    configs = []
    for cfg in root.findall("Config"):
        configs.append(
            {
                "name": cfg.get("Name"),
                "target": cfg.get("Target"),
                "type": cfg.get("Type"),
                "mode": cfg.get("Mode"),
                "value": (cfg.text or "").strip(),
            }
        )

    return TemplateInfo(
        icon=text("Icon"),
        webui=text("WebUI"),
        category=text("Category"),
        overview=text("Overview"),
        configs=configs,
    )


def friendly_name_for_path(configs: list[dict[str, Any]] | None, container_path: str) -> str | None:
    if not configs:
        return None
    for cfg in configs:
        if cfg.get("type") == "Path" and cfg.get("target") == container_path:
            return cfg.get("name")
    return None


def resolve_webui_url(webui_template: str, host_ip: str, ports: dict[str, str]) -> str | None:
    """WebUI field looks like 'http://[IP]:[PORT:80]/'. Substitute the real
    host IP and the *host* port that container port 80 is published as."""
    if not webui_template:
        return None
    url = webui_template.replace("[IP]", host_ip)
    import re

    def repl(match: "re.Match[str]") -> str:
        container_port = match.group(1)
        return ports.get(container_port, container_port)

    url = re.sub(r"\[PORT:(\d+)\]", repl, url)
    return url
