"""Thin wrapper around docker-py for everything the app needs: listing
containers, reading their full low-level config, and recreating a container
from a previously-captured config (the core of "restore")."""

from __future__ import annotations

import functools
import logging
from typing import Any, Iterable

import docker
from docker.errors import NotFound

from .jobs import JobCancelled

log = logging.getLogger("docker_safeguard.docker_client")


class DockerUnavailable(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def _client() -> docker.DockerClient:
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001
        raise DockerUnavailable(
            "Could not reach the Docker socket. Is /var/run/docker.sock mounted?"
        ) from exc


def api():
    return _client().api


def is_available() -> bool:
    try:
        _client()
        return True
    except DockerUnavailable:
        return False


def list_containers() -> list[dict[str, Any]]:
    """Summary info for the dashboard: one row per container, all states."""
    out = []
    for c in _client().containers.list(all=True):
        attrs = c.attrs
        config = attrs.get("Config", {}) or {}
        labels = config.get("Labels") or {}
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "image": config.get("Image", ""),
                "status": c.status,  # running, exited, created, ...
                "state": attrs.get("State", {}),
                "labels": labels,
                "icon": labels.get("net.unraid.docker.icon"),
                "webui": labels.get("net.unraid.docker.webui"),
            }
        )
    out.sort(key=lambda r: r["name"].lower())
    return out


def inspect(name: str) -> dict[str, Any]:
    try:
        return api().inspect_container(name)
    except NotFound as exc:
        raise KeyError(name) from exc


def container_exists(name: str) -> bool:
    try:
        inspect(name)
        return True
    except KeyError:
        return False


def stop(name: str, timeout: int = 30) -> None:
    try:
        _client().containers.get(name).stop(timeout=timeout)
    except NotFound:
        pass


def start(name: str) -> None:
    _client().containers.get(name).start()


def remove(name: str, force: bool = False) -> None:
    try:
        _client().containers.get(name).remove(force=force)
    except NotFound:
        pass


def is_running(name: str) -> bool:
    try:
        return inspect(name)["State"]["Running"]
    except KeyError:
        return False


def pull_image(image_ref: str, log_cb=None, cancel_check=None) -> None:
    """image_ref like 'repo/name:tag'. Streams progress lines to log_cb.
    If cancel_check() becomes true mid-pull, stops reading the stream
    (best-effort: the daemon may finish the pull server-side regardless,
    but we treat it as cancelled from the app's point of view)."""
    if ":" in image_ref.rsplit("/", 1)[-1]:
        repo, tag = image_ref.rsplit(":", 1)
    else:
        repo, tag = image_ref, "latest"
    last_status = None
    for line in api().pull(repo, tag=tag, stream=True, decode=True):
        if cancel_check and cancel_check():
            raise JobCancelled()
        status = line.get("status")
        progress = line.get("progress", "")
        if status and status != last_status and log_cb:
            log_cb(f"pull: {status} {progress}".strip())
            last_status = status
        if "error" in line:
            raise RuntimeError(f"Failed to pull {image_ref}: {line['error']}")


def image_exists_locally(image_ref: str) -> bool:
    try:
        _client().images.get(image_ref)
        return True
    except docker.errors.ImageNotFound:
        return False


# ---------------------------------------------------------------------------
# Recreating a container from a captured docker-inspect dict.
# ---------------------------------------------------------------------------


def _convert_port_bindings(raw: dict | None) -> dict | None:
    if not raw:
        return None
    out: dict[str, Any] = {}
    for container_port, bindings in raw.items():
        if not bindings:
            continue
        entries = []
        for b in bindings:
            host_ip = b.get("HostIp") or ""
            host_port = b.get("HostPort") or ""
            entries.append((host_ip, host_port) if host_ip else host_port)
        out[container_port] = entries if len(entries) > 1 else entries[0]
    return out or None


def _convert_extra_hosts(raw: list | None) -> dict | None:
    if not raw:
        return None
    out = {}
    for entry in raw:
        if ":" in entry:
            host, ip = entry.split(":", 1)
            out[host] = ip
    return out or None


def _convert_devices(raw: list | None) -> list[str] | None:
    if not raw:
        return None
    out = []
    for d in raw:
        out.append(
            f"{d.get('PathOnHost')}:{d.get('PathOnContainer')}:{d.get('CgroupPermissions', 'rwm')}"
        )
    return out or None


def build_host_config_kwargs(inspect_data: dict) -> dict:
    hc = inspect_data.get("HostConfig", {}) or {}
    kwargs: dict[str, Any] = {}

    if hc.get("Binds"):
        kwargs["binds"] = hc["Binds"]
    port_bindings = _convert_port_bindings(hc.get("PortBindings"))
    if port_bindings:
        kwargs["port_bindings"] = port_bindings
    if hc.get("RestartPolicy") and hc["RestartPolicy"].get("Name"):
        kwargs["restart_policy"] = hc["RestartPolicy"]
    network_mode = hc.get("NetworkMode")
    if network_mode and network_mode != "default":
        kwargs["network_mode"] = network_mode
    devices = _convert_devices(hc.get("Devices"))
    if devices:
        kwargs["devices"] = devices
    if hc.get("CapAdd"):
        kwargs["cap_add"] = hc["CapAdd"]
    if hc.get("CapDrop"):
        kwargs["cap_drop"] = hc["CapDrop"]
    if hc.get("Privileged"):
        kwargs["privileged"] = True
    if hc.get("Dns"):
        kwargs["dns"] = hc["Dns"]
    extra_hosts = _convert_extra_hosts(hc.get("ExtraHosts"))
    if extra_hosts:
        kwargs["extra_hosts"] = extra_hosts
    if hc.get("Memory"):
        kwargs["mem_limit"] = hc["Memory"]
    if hc.get("ShmSize"):
        kwargs["shm_size"] = hc["ShmSize"]
    if hc.get("SecurityOpt"):
        kwargs["security_opt"] = hc["SecurityOpt"]
    if hc.get("Sysctls"):
        kwargs["sysctls"] = hc["Sysctls"]
    return kwargs


def build_networking_config(inspect_data: dict):
    """Preserve a static IP on a custom/macvlan network (e.g. unRAID's br0),
    if one was assigned. Returns None for plain bridge/host containers."""
    networks = (inspect_data.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
    endpoints = {}
    for net_name, net in networks.items():
        if net_name in ("bridge", "host", "none"):
            continue
        ipam = net.get("IPAMConfig") or {}
        ipv4 = ipam.get("IPv4Address")
        aliases = net.get("Aliases") or None
        if ipv4:
            endpoints[net_name] = api().create_endpoint_config(
                ipv4_address=ipv4, aliases=aliases
            )
    if not endpoints:
        return None
    return api().create_networking_config(endpoints)


def build_create_kwargs(inspect_data: dict, name: str) -> dict:
    config = inspect_data.get("Config", {}) or {}
    host_config_kwargs = build_host_config_kwargs(inspect_data)
    host_config = api().create_host_config(**host_config_kwargs)

    exposed_ports = list((config.get("ExposedPorts") or {}).keys())
    ports = []
    for p in exposed_ports:
        if "/" in p:
            port_num, proto = p.split("/", 1)
        else:
            port_num, proto = p, "tcp"
        ports.append((port_num, proto))

    volumes = []
    for bind in host_config_kwargs.get("binds", []):
        parts = bind.split(":")
        if len(parts) >= 2:
            volumes.append(parts[1])

    kwargs: dict[str, Any] = dict(
        image=config.get("Image"),
        name=name,
        command=config.get("Cmd"),
        entrypoint=config.get("Entrypoint"),
        environment=config.get("Env") or [],
        labels=config.get("Labels") or {},
        working_dir=config.get("WorkingDir") or None,
        user=config.get("User") or None,
        tty=bool(config.get("Tty")),
        stdin_open=bool(config.get("OpenStdin")),
        ports=ports or None,
        volumes=volumes or None,
        host_config=host_config,
        detach=True,
    )
    hostname = config.get("Hostname")
    if hostname and host_config_kwargs.get("network_mode") != "host":
        kwargs["hostname"] = hostname

    networking_config = build_networking_config(inspect_data)
    if networking_config:
        kwargs["networking_config"] = networking_config

    return {k: v for k, v in kwargs.items() if v is not None}


def create_container_from_inspect(inspect_data: dict, name: str) -> str:
    """Creates and starts a new container mirroring a captured inspect dict.
    Returns the new container ID."""
    kwargs = build_create_kwargs(inspect_data, name)
    kwargs.pop("detach", None)
    result = api().create_container(**kwargs)
    container_id = result["Id"]
    api().start(container_id)
    return container_id


def bind_mounts(inspect_data: dict) -> list[dict[str, Any]]:
    """Returns only real bind mounts (host directories) - not named volumes,
    tmpfs, etc - since those are what appdata backups care about."""
    out = []
    for m in inspect_data.get("Mounts", []) or []:
        if m.get("Type") != "bind":
            continue
        out.append(
            {
                "source": m.get("Source"),
                "destination": m.get("Destination"),
                "mode": m.get("Mode") or ("rw" if m.get("RW", True) else "ro"),
            }
        )
    return out


def named_volumes(inspect_data: dict) -> list[dict[str, Any]]:
    out = []
    for m in inspect_data.get("Mounts", []) or []:
        if m.get("Type") == "volume":
            out.append({"name": m.get("Name"), "destination": m.get("Destination")})
    return out
