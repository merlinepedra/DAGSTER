import json
import os
import sys
import time

from dagster._core.storage.compute_log_manager import ComputeIOType
from dagster._serdes.serdes import deserialize_json_to_dagster_namedtuple
from dagster._utils.interrupts import capture_interrupts, pop_captured_interrupt


def current_process_is_orphaned(parent_pid):
    parent_pid = int(parent_pid)
    if sys.platform == "win32":
        import psutil  # pylint: disable=import-error

        try:
            parent = psutil.Process(parent_pid)
            return parent.status() != psutil.STATUS_RUNNING
        except psutil.NoSuchProcess:
            return True

    else:
        return os.getppid() != parent_pid


def execute_polling(args):
    if not args or len(args) != 4:
        return

    parent_pid = int(args[0])
    compute_log_manager = deserialize_json_to_dagster_namedtuple(args[1]).rehydrate()
    log_key = json.loads(args[2])
    interval = int(args[3])

    while not compute_log_manager.is_capture_complete(log_key):
        if pop_captured_interrupt() or (parent_pid and current_process_is_orphaned(parent_pid)):
            return

        time.sleep(interval)
        compute_log_manager.on_progress(log_key)


if __name__ == "__main__":
    with capture_interrupts():
        execute_polling(sys.argv[1:])
