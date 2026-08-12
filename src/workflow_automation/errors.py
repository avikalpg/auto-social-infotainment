from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    USAGE = 2
    CONFIG = 3
    LOCKED = 4
    VALIDATION = 5
    ADAPTER_NOT_CONFIGURED = 6
    RETRY_EXHAUSTED = 7
    SUBPROCESS = 8
    STATE = 9
