from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any

from . import docker_client
from .config import SCHEMA_VERSION, TOOL_VERSION
from .paths import classify_data_paths
from .template_xml import read_template


def build_manifest(container_name: str, data_path_selection: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """data_path_selection: optional override of which archive_member entries
    are included (from the advanced options form). Defaults to the
    appdata-only auto-classification."""
    inspect_data = docker_client.inspect(container_name)
    mounts = docker_client.bind_mounts(inspect_data)
    data_paths = classify_data_paths(mounts)

    if data_path_selection is not None:
        overrides = {int(p["index"]): bool(p["included"]) for p in data_path_selection}
        for entry in data_paths:
            if entry["index"] in overrides:
                entry["included"] = overrides[entry["index"]]
            else:
                entry["included"] = entry["included_default"]
    else:
        for entry in data_paths:
            entry["included"] = entry["included_default"]

    template_xml = read_template(container_name)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "backup_id": f"{container_name}__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_server": socket.gethostname(),
        "container": {
            "name": container_name,
            "image": inspect_data.get("Config", {}).get("Image"),
            "image_id": inspect_data.get("Image"),
            "raw_inspect": inspect_data,
        },
        "template_xml": template_xml,
        "data_paths": data_paths,
        "named_volumes": docker_client.named_volumes(inspect_data),
    }
    return manifest


def archive_filename(manifest: dict[str, Any]) -> str:
    from .config import ARCHIVE_SUFFIX

    return f"{manifest['backup_id']}{ARCHIVE_SUFFIX}"
