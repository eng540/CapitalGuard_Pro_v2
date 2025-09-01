# --- START OF FILE: src/capitalguard/interfaces/telegram/auth.py ---
from telegram.ext import filters
from capitalguard.config import settings

def get_allowed_user_ids() -> list[int]:
    """
    Parses the TELEGRAM_ALLOWED_USERS environment variable and returns a list of integer user IDs.
    The variable should be a comma-separated string of numbers (e.g., "12345,67890").
    """
    allowed_users_str = settings.TELEGRAM_ALLOWED_USERS
    if not allowed_users_str:
        # If the variable is not set, return an empty list, effectively blocking all users
        # except for maybe system administrators in a more complex setup.
        # For this project, it means no one can use the commands if not set.
        return []
    
    user_ids = []
    # Split the string by commas and strip any whitespace
    parts = [part.strip() for part in allowed_users_str.split(',')]
    for part in parts:
        if part.isdigit():
            user_ids.append(int(part))
    return user_ids

# --- The Filter ---
# This filter will be imported and used by command handlers to restrict access.
# It checks if the user ID of the person sending the message is in our allowed list.

# Get the list of allowed user IDs once when the module is loaded.
_allowed_ids = get_allowed_user_ids()

# Create a filter. If the list is empty, the filter will reject all users.
# The `from_user` filter can take a list of IDs directly.
ALLOWED_FILTER = filters.User(user_id=_allowed_ids)
# --- END OF FILE ---