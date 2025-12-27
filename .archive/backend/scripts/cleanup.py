#!/usr/bin/env python3
"""Cleanup script for development workspace containers.

Removes leftover codehub-ws-* containers and optionally the codehub-net network.
"""

import argparse
import sys
from typing import Any

import docker
from docker.errors import NotFound

CONTAINER_PREFIX = "codehub-ws-"
NETWORK_NAME = "codehub-net"


def get_workspace_containers(client: docker.DockerClient) -> list[Any]:
    """List all workspace containers (codehub-ws-* prefix)."""
    containers = client.containers.list(all=True)
    return [c for c in containers if c.name and c.name.startswith(CONTAINER_PREFIX)]


def cleanup_containers(
    client: docker.DockerClient,
    dry_run: bool = False,
) -> int:
    """Remove all workspace containers.

    Returns:
        Number of containers removed.
    """
    containers = get_workspace_containers(client)

    if not containers:
        print("No workspace containers found.")
        return 0

    print(f"Found {len(containers)} workspace container(s):")
    for c in containers:
        status = c.status
        print(f"  - {c.name} ({status})")

    if dry_run:
        print("\n[dry-run] No containers removed.")
        return 0

    removed = 0
    for c in containers:
        try:
            print(f"Removing: {c.name}...", end=" ")
            c.remove(force=True)
            print("done")
            removed += 1
        except Exception as e:
            print(f"failed: {e}")

    print(f"\nRemoved {removed} container(s).")
    return removed


def cleanup_network(
    client: docker.DockerClient,
    dry_run: bool = False,
) -> bool:
    """Remove codehub-net network if it exists and has no containers.

    Returns:
        True if network was removed, False otherwise.
    """
    try:
        network = client.networks.get(NETWORK_NAME)
    except NotFound:
        print(f"Network '{NETWORK_NAME}' not found.")
        return False

    # Check if network has attached containers
    network.reload()
    attached = network.attrs.get("Containers", {})
    if attached:
        names = [info.get("Name", cid[:12]) for cid, info in attached.items()]
        print(f"Network '{NETWORK_NAME}' has attached containers: {', '.join(names)}")
        print("Cannot remove network while containers are attached.")
        return False

    if dry_run:
        print(f"[dry-run] Would remove network: {NETWORK_NAME}")
        return False

    print(f"Removing network: {NETWORK_NAME}...", end=" ")
    try:
        network.remove()
        print("done")
        return True
    except Exception as e:
        print(f"failed: {e}")
        return False


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Cleanup development workspace containers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --dry-run          # Show what would be removed
  %(prog)s --force            # Remove without confirmation
  %(prog)s --include-network  # Also remove codehub-net network
""",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without actually removing",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Remove without confirmation prompt",
    )
    parser.add_argument(
        "--include-network",
        action="store_true",
        help="Also remove codehub-net network (if empty)",
    )

    args = parser.parse_args()

    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        print(f"Error: Cannot connect to Docker: {e}", file=sys.stderr)
        return 1

    # List containers
    containers = get_workspace_containers(client)

    if not containers and not args.include_network:
        print("No workspace containers found. Nothing to clean up.")
        return 0

    # Confirmation prompt (unless --force or --dry-run)
    if containers and not args.force and not args.dry_run:
        print(f"Found {len(containers)} workspace container(s):")
        for c in containers:
            print(f"  - {c.name} ({c.status})")
        print()
        response = input("Remove these containers? [y/N] ")
        if response.lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    # Cleanup containers
    cleanup_containers(client, dry_run=args.dry_run)

    # Cleanup network (optional)
    if args.include_network:
        print()
        cleanup_network(client, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
