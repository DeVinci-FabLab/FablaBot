"""Initialization module for the fablabot package."""

from role_loader import can_assign_role, load_roles

__all__ = ["can_assign_role", "load_roles"]

if __name__ == "__main__":
    from fablabot.main import main

    main()
