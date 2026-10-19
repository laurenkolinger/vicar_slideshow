"""Module: vicar_showcase.common
Purpose: Constants that more than one part of the tool shares, each defined once:
    process exit codes, the loopback host, the megabyte size and the command timeout.
Inputs: None.
Outputs: Named constants.
"""

EXIT_OK = 0
EXIT_USAGE = 1  # settings or usage error; also a port held by another program, or serve failing to bind
EXIT_NO_CONTENT = 2  # a folder (or a --captions file) that must exist does not
EXIT_REFUSED = 3  # copy refused: conflicts, no free space, or nested folders
EXIT_FAILED = 4  # copy finished with failed files

LOOPBACK_HOST = "127.0.0.1"
LOCAL_HOST_NAMES = (LOOPBACK_HOST, "localhost")  # the only Host header names the server answers to
BYTES_PER_MB = 1_000_000
COMMAND_TIMEOUT_SECONDS = 5  # gsettings, wmctrl and server-stop waits
