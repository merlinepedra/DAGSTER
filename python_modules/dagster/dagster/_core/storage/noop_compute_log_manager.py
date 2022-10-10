from contextlib import contextmanager
from typing import List, Optional

import dagster._check as check
from dagster._core.storage.captured_log_manager import (
    CapturedLogData,
    CapturedLogManager,
    CapturedLogMetadata,
    CapturedLogSubscription,
)
from dagster._serdes import ConfigurableClass, ConfigurableClassData

from .compute_log_manager import MAX_BYTES_FILE_READ, ComputeLogFileData, ComputeLogManager


class NoOpComputeLogManager(CapturedLogManager, ComputeLogManager, ConfigurableClass):
    def __init__(self, inst_data=None):
        self._inst_data = check.opt_inst_param(inst_data, "inst_data", ConfigurableClassData)

    @property
    def inst_data(self):
        return self._inst_data

    @classmethod
    def config_type(cls):
        return {}

    @staticmethod
    def from_config_value(inst_data, config_value):
        return NoOpComputeLogManager(inst_data=inst_data, **config_value)

    def enabled(self, _pipeline_run, _step_key):
        return False

    def _watch_logs(self, pipeline_run, step_key=None):
        pass

    def is_watch_completed(self, run_id, key):
        return True

    def on_watch_start(self, pipeline_run, step_key):
        pass

    def on_watch_finish(self, pipeline_run, step_key):
        pass

    def download_url(self, run_id, key, io_type):
        return None

    def read_logs_file(self, run_id, key, io_type, cursor=0, max_bytes=MAX_BYTES_FILE_READ):
        return ComputeLogFileData(
            path="{}.{}".format(key, io_type), data=None, cursor=0, size=0, download_url=None
        )

    def on_subscribe(self, subscription):
        pass

    def on_unsubscribe(self, subscription):
        pass

    @contextmanager
    def capture_logs(self, log_key: List[str]):
        yield

    def is_capture_complete(self, log_key: List[str]):
        return True

    def get_log_data(
        self,
        log_key: List[str],
        cursor: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ) -> CapturedLogData:
        return CapturedLogData(log_key=log_key)

    def get_contextual_log_metadata(self, log_key: List[str]) -> CapturedLogMetadata:
        return CapturedLogMetadata()

    def on_progress(self, log_key: List[str]):
        pass

    def subscribe(
        self, log_key: List[str], cursor: Optional[str] = None
    ) -> CapturedLogSubscription:
        return CapturedLogSubscription(self, log_key, cursor)

    def unsubscribe(self, subscription: CapturedLogSubscription):
        pass
