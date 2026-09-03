from enum import Enum
from typing import List, Dict, Set

class Permission(Enum):
    EXECUTE_COMMAND = "execute:command"
    READ_STATUS = "read:status"
    TRIGGER_UPDATE = "maintenance:update"
    TRIGGER_RESTART = "maintenance:restart"

# Role to Permission mapping
ROLE_PERMISSIONS: Dict[str, Set[Permission]] = {
    "admin": {
        Permission.EXECUTE_COMMAND,
        Permission.READ_STATUS,
        Permission.TRIGGER_UPDATE,
        Permission.TRIGGER_RESTART,
    },
    "operator": {
        Permission.EXECUTE_COMMAND, # Potentially with a command whitelist
        Permission.READ_STATUS,
        Permission.TRIGGER_RESTART,
    },
    "viewer": {
        Permission.READ_STATUS,
    }
}