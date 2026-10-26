# Copyright (c) 2021 - JoJo
# Copyright (c) 2026 - SinOfStrife
# Licensed under MIT

from typing import Dict, Final

from ._types import ConfigType

__all__ = ("__author__", "__version__", "config_structure", "default_format")


__author__ = "SinOfStrife"
__version__ = "3.1.0"

default_format: Final[Dict[str, str]] = {
    "title": "{allow_deny_list}",
    "user_or_role": "{index}. {user_or_role}: {reason}",
    "footer": "{bot_name} running AdvancedBlacklist {version_info}",
}
config_structure: Final[Dict[str, Dict[str, ConfigType]]] = {
    "global": {
        "blacklist": {},
        "whitelist": {},
        "schema_v1": 1,
        "log_channel": None,
        "format": default_format,
    },
    "guild": {
        "blacklist": {},
        "whitelist": {},
        "log_channel": None,
    },
}
