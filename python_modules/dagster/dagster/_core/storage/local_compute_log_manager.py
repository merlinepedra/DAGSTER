import hashlib
import os
import sys
from collections import defaultdict
from contextlib import contextmanager
from typing import List, Optional
from urllib.parse import urljoin

from watchdog.events import PatternMatchingEventHandler
from watchdog.observers.polling import PollingObserver

from dagster import Field, Float, StringSource
from dagster import _check as check
from dagster._core.execution.compute_logs import mirror_stream_to_file
from dagster._core.storage.pipeline_run import PipelineRun
from dagster._serdes import ConfigurableClass, ConfigurableClassData
from dagster._utils import ensure_dir, touch_file

from .captured_log_manager import CapturedLogData, CapturedLogManager, CapturedLogMetadata
from .compute_log_manager import (
    MAX_BYTES_FILE_READ,
    ComputeIOType,
    ComputeLogFileData,
    ComputeLogManager,
    ComputeLogSubscription,
)

DEFAULT_WATCHDOG_POLLING_TIMEOUT = 2.5

IO_TYPE_EXTENSION = {ComputeIOType.STDOUT: "out", ComputeIOType.STDERR: "err"}

MAX_FILENAME_LENGTH = 255


class LocalComputeLogManager(CapturedLogManager, ComputeLogManager, ConfigurableClass):
    """Stores copies of stdout & stderr for each compute step locally on disk."""

    def __init__(self, base_dir, polling_timeout=None, inst_data=None):
        self._base_dir = base_dir
        self._polling_timeout = check.opt_float_param(
            polling_timeout, "polling_timeout", DEFAULT_WATCHDOG_POLLING_TIMEOUT
        )
        self._subscription_manager = LocalComputeLogSubscriptionManager(self)
        self._inst_data = check.opt_inst_param(inst_data, "inst_data", ConfigurableClassData)

    @property
    def inst_data(self):
        return self._inst_data

    @property
    def polling_timeout(self):
        return self._polling_timeout

    @classmethod
    def config_type(cls):
        return {
            "base_dir": StringSource,
            "polling_timeout": Field(Float, is_required=False),
        }

    @staticmethod
    def from_config_value(inst_data, config_value):
        return LocalComputeLogManager(inst_data=inst_data, **config_value)

    @contextmanager
    def capture_logs(self, log_key: List[str]):
        outpath = self._get_captured_local_path(log_key, IO_TYPE_EXTENSION[ComputeIOType.STDOUT])
        errpath = self._get_captured_local_path(log_key, IO_TYPE_EXTENSION[ComputeIOType.STDERR])
        with mirror_stream_to_file(sys.stdout, outpath):
            with mirror_stream_to_file(sys.stderr, errpath):
                yield

        # leave artifact on filesystem so that we know the capture is completed
        touch_file(self.complete_artifact_path(log_key))

    def is_capture_complete(self, log_key: List[str]):
        return os.path.exists(self.complete_artifact_path(log_key))

    def get_stdout(
        self,
        log_key: List[str],
        cursor: str = None,
        max_bytes: int = None,
    ) -> CapturedLogData:
        path = self._get_captured_local_path(log_key, IO_TYPE_EXTENSION[ComputeIOType.STDOUT])
        return self._read_path(path, cursor=cursor, max_bytes=max_bytes)

    def get_stderr(
        self,
        log_key: List[str],
        cursor: str = None,
        max_bytes: int = None,
    ) -> CapturedLogData:
        path = self._get_captured_local_path(log_key, IO_TYPE_EXTENSION[ComputeIOType.STDERR])
        return self._read_path(path, cursor=cursor, max_bytes=max_bytes)

    def get_stdout_metadata(self, log_key: List[str]) -> CapturedLogMetadata:
        return CapturedLogMetadata(
            location=self._get_captured_local_path(
                log_key, IO_TYPE_EXTENSION[ComputeIOType.STDOUT]
            ),
            download_url=self._captured_log_download_url(log_key, ComputeIOType.STDOUT),
        )

    def get_stderr_metadata(self, log_key: List[str]) -> CapturedLogMetadata:
        return CapturedLogMetadata(
            location=self._get_captured_local_path(
                log_key, IO_TYPE_EXTENSION[ComputeIOType.STDERR]
            ),
            download_url=self._captured_log_download_url(log_key, ComputeIOType.STDERR),
        )

    def complete_artifact_path(self, log_key):
        return self._get_captured_local_path(log_key, "complete")

    def _read_path(self, path: str, cursor: Optional[str] = None, max_bytes: Optional[int] = None):
        if not os.path.exists(path) or not os.path.isfile(path):
            return CapturedLogData()

        with open(path, "rb") as f:
            offset = 0
            if cursor:
                try:
                    offset = int(cursor)
                except ValueError:
                    pass
            f.seek(offset, os.SEEK_SET)
            if max_bytes is None:
                data = f.read()
            else:
                data = f.read(max_bytes)
            new_offset = f.tell()

        return CapturedLogData(chunk=data, cursor=new_offset)

    def _captured_log_download_url(self, log_key, io_type):
        check.inst_param(io_type, "io_type", ComputeIOType)
        url = "/logs"
        for part in log_key:
            url = urljoin(url, part)

        url = urljoin(url, io_type.value)
        return url

    def _get_captured_local_path(self, log_key: List[str], extension: str):
        [*namespace, filebase] = log_key
        filename = f"{filebase}.{extension}"
        if len(filename) > MAX_FILENAME_LENGTH:
            filename = "{}.{}".format(hashlib.md5(filebase.encode("utf-8")).hexdigest(), extension)
        return os.path.join(self._base_dir, *namespace, filename)

    ###############################################
    #
    # Methods for the ComputeLogManager interface
    #
    ###############################################
    @contextmanager
    def _watch_logs(self, pipeline_run, step_key=None):
        check.inst_param(pipeline_run, "pipeline_run", PipelineRun)
        check.opt_str_param(step_key, "step_key")

        log_key = self.build_log_key(pipeline_run.run_id, step_key or pipeline_run.pipeline_name)
        with self.capture_logs(log_key):
            yield

    def build_log_key(self, run_id, key):
        """Legacy adapter to translate run_id/key to captured log manager-based log_key"""
        return [run_id, "compute_logs", key]

    def get_local_path(self, run_id, key, io_type):
        """Legacy adapter from compute log manager to more generic captured log manager API"""
        check.inst_param(io_type, "io_type", ComputeIOType)
        log_key = self.build_log_key(run_id, key)
        return self._get_captured_local_path(log_key, IO_TYPE_EXTENSION[io_type])

    def read_logs_file(self, run_id, key, io_type, cursor=0, max_bytes=MAX_BYTES_FILE_READ):
        path = self.get_local_path(run_id, key, io_type)

        if not os.path.exists(path) or not os.path.isfile(path):
            return ComputeLogFileData(path=path, data=None, cursor=0, size=0, download_url=None)

        # See: https://docs.python.org/2/library/stdtypes.html#file.tell for Windows behavior
        with open(path, "rb") as f:
            f.seek(cursor, os.SEEK_SET)
            data = f.read(max_bytes)
            cursor = f.tell()
            stats = os.fstat(f.fileno())

        # local download path
        download_url = self.download_url(run_id, key, io_type)
        return ComputeLogFileData(
            path=path,
            data=data.decode("utf-8"),
            cursor=cursor,
            size=stats.st_size,
            download_url=download_url,
        )

    def get_key(self, pipeline_run, step_key):
        check.inst_param(pipeline_run, "pipeline_run", PipelineRun)
        check.opt_str_param(step_key, "step_key")
        return step_key or pipeline_run.pipeline_name

    def is_watch_completed(self, run_id, key):
        log_key = self.build_log_key(run_id, key)
        return self.is_capture_complete(log_key)

    def on_watch_start(self, pipeline_run, step_key):
        pass

    def on_watch_finish(self, pipeline_run, step_key=None):
        check.inst_param(pipeline_run, "pipeline_run", PipelineRun)
        check.opt_str_param(step_key, "step_key")
        log_key = self.build_log_key(pipeline_run.run_id, step_key or pipeline_run.pipeline_name)
        touchpath = self.complete_artifact_path(log_key)
        touch_file(touchpath)

    def download_url(self, run_id, key, io_type):
        check.inst_param(io_type, "io_type", ComputeIOType)
        return "/download/{}/{}/{}".format(run_id, key, io_type.value)

    def on_subscribe(self, subscription):
        self._subscription_manager.add_subscription(subscription)

    def on_unsubscribe(self, subscription):
        self._subscription_manager.remove_subscription(subscription)

    def dispose(self):
        self._subscription_manager.dispose()


class LocalComputeLogSubscriptionManager:
    def __init__(self, manager):
        self._manager = manager
        self._subscriptions = defaultdict(list)
        self._watchers = {}
        self._observer = None

    def _watch_key(self, run_id, key):
        return "{}:{}".format(run_id, key)

    def add_subscription(self, subscription):
        check.inst_param(subscription, "subscription", ComputeLogSubscription)
        if self._manager.is_watch_completed(subscription.run_id, subscription.key):
            subscription.fetch()
            subscription.complete()
        else:
            watch_key = self._watch_key(subscription.run_id, subscription.key)
            self._subscriptions[watch_key].append(subscription)
            self.watch(subscription.run_id, subscription.key)

    def remove_subscription(self, subscription):
        check.inst_param(subscription, "subscription", ComputeLogSubscription)
        watch_key = self._watch_key(subscription.run_id, subscription.key)
        if subscription in self._subscriptions[watch_key]:
            self._subscriptions[watch_key].remove(subscription)
            subscription.complete()

    def remove_all_subscriptions(self, run_id, step_key):
        watch_key = self._watch_key(run_id, step_key)
        for subscription in self._subscriptions.pop(watch_key, []):
            subscription.complete()

    def watch(self, run_id, step_key):
        watch_key = self._watch_key(run_id, step_key)
        if watch_key in self._watchers:
            return

        update_paths = [
            self._manager.get_local_path(run_id, step_key, ComputeIOType.STDOUT),
            self._manager.get_local_path(run_id, step_key, ComputeIOType.STDERR),
        ]
        complete_paths = [
            self._manager.complete_artifact_path(self._manager.build_log_key(run_id, step_key))
        ]
        directory = os.path.dirname(
            self._manager.get_local_path(run_id, step_key, ComputeIOType.STDERR)
        )

        if not self._observer:
            self._observer = PollingObserver(self._manager.polling_timeout)
            self._observer.start()

        ensure_dir(directory)

        self._watchers[watch_key] = self._observer.schedule(
            LocalComputeLogFilesystemEventHandler(
                self, run_id, step_key, update_paths, complete_paths
            ),
            str(directory),
        )

    def notify_subscriptions(self, run_id, step_key):
        watch_key = self._watch_key(run_id, step_key)
        for subscription in self._subscriptions[watch_key]:
            subscription.fetch()

    def unwatch(self, run_id, step_key, handler):
        watch_key = self._watch_key(run_id, step_key)
        if watch_key in self._watchers:
            self._observer.remove_handler_for_watch(handler, self._watchers[watch_key])
        del self._watchers[watch_key]

    def dispose(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(15)


class LocalComputeLogFilesystemEventHandler(PatternMatchingEventHandler):
    def __init__(self, manager, run_id, key, update_paths, complete_paths):
        self.manager = manager
        self.run_id = run_id
        self.key = key
        self.update_paths = update_paths
        self.complete_paths = complete_paths
        patterns = update_paths + complete_paths
        super(LocalComputeLogFilesystemEventHandler, self).__init__(patterns=patterns)

    def on_created(self, event):
        if event.src_path in self.complete_paths:
            self.manager.remove_all_subscriptions(self.run_id, self.key)
            self.manager.unwatch(self.run_id, self.key, self)

    def on_modified(self, event):
        if event.src_path in self.update_paths:
            self.manager.notify_subscriptions(self.run_id, self.key)
