"""User management CLI commands."""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.db import User
from app.db.session import get_engine


async def create_user(username: str, password: str) -> None:
    """Create a new user."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        # Check if user exists
        result = await session.execute(
            select(User).where(
                User.username == username  # type: ignore[arg-type]
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Error: User '{username}' already exists")
            sys.exit(1)

        user = User(
            username=username,
            password_hash=hash_password(password),
        )
        session.add(user)
        await session.commit()
        print(f"User '{username}' created successfully")


async def reset_password(username: str, password: str) -> None:
    """Reset user password."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.username == username  # type: ignore[arg-type]
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            print(f"Error: User '{username}' not found")
            sys.exit(1)

        user.password_hash = hash_password(password)
        await session.commit()
        print(f"Password reset for '{username}'")


async def list_users() -> None:
    """List all users."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        if not users:
            print("No users found")
            return

        print(f"{'Username':<20} {'Created At':<25}")
        print("-" * 45)
        for user in users:
            created = user.created_at.strftime("%Y-%m-%d %H:%M:%S") if user.created_at else "N/A"
            print(f"{user.username:<20} {created:<25}")


async def delete_user(username: str) -> None:
    """Delete a user."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_factory() as session:
        result = await session.execute(
            select(User).where(
                User.username == username  # type: ignore[arg-type]
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            print(f"Error: User '{username}' not found")
            sys.exit(1)

        await session.delete(user)
        await session.commit()
        print(f"User '{username}' deleted")


def get_password_interactive(confirm: bool = True) -> str:
    """Get password interactively with optional confirmation."""
    password = getpass.getpass("Password: ")
    if not password:
        print("Error: Password cannot be empty")
        sys.exit(1)

    if confirm:
        password_confirm = getpass.getpass("Confirm password: ")
        if password != password_confirm:
            print("Error: Passwords do not match")
            sys.exit(1)

    return password


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CodeHub user management",
        prog="codehub-user",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # create command
    create_parser = subparsers.add_parser("create", help="Create a new user")
    create_parser.add_argument("username", help="Username to create")
    create_parser.add_argument(
        "--password", "-p",
        help="Password (will prompt if not provided)",
    )

    # reset-password command
    reset_parser = subparsers.add_parser("reset-password", help="Reset user password")
    reset_parser.add_argument("username", help="Username to reset password")
    reset_parser.add_argument(
        "--password", "-p",
        help="New password (will prompt if not provided)",
    )

    # list command
    subparsers.add_parser("list", help="List all users")

    # delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a user")
    delete_parser.add_argument("username", help="Username to delete")
    delete_parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Skip confirmation",
    )

    args = parser.parse_args()

    if args.command == "create":
        password = args.password or get_password_interactive()
        asyncio.run(create_user(args.username, password))

    elif args.command == "reset-password":
        password = args.password or get_password_interactive()
        asyncio.run(reset_password(args.username, password))

    elif args.command == "list":
        asyncio.run(list_users())

    elif args.command == "delete":
        if not args.force:
            confirm = input(f"Delete user '{args.username}'? [y/N]: ")
            if confirm.lower() != "y":
                print("Cancelled")
                sys.exit(0)
        asyncio.run(delete_user(args.username))


if __name__ == "__main__":
    main()
