import json
import os
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import List, Optional, Union

import boto3
from botocore.errorfactory import ClientError

import dagster._seven as seven
from dagster import Field, StringSource
from dagster import _check as check
from dagster._core.execution.poll_compute_logs import POLLING_INTERVAL
from dagster._core.storage.captured_log_manager import (
    CapturedLogData,
    CapturedLogManager,
    CapturedLogMetadata,
    CapturedLogSubscription,
)
from dagster._core.storage.compute_log_manager import (
    MAX_BYTES_FILE_READ,
    ComputeIOType,
    ComputeLogFileData,
    ComputeLogManager,
    ComputeLogSubscription,
)
from dagster._core.storage.local_compute_log_manager import (
    IO_TYPE_EXTENSION,
    LocalComputeLogManager,
)
from dagster._serdes import ConfigurableClass, ConfigurableClassData, serialize_dagster_namedtuple
from dagster._serdes.ipc import interrupt_ipc_subprocess, open_ipc_subprocess
from dagster._seven import wait_for_process
from dagster._utils import ensure_dir, ensure_file

from . import poll_upload

POLLING_INTERVAL = 5


class S3ComputeLogManager(CapturedLogManager, ComputeLogManager, ConfigurableClass):
    """Logs compute function stdout and stderr to S3.

    Users should not instantiate this class directly. Instead, use a YAML block in ``dagster.yaml``
    such as the following:

    .. code-block:: YAML

        compute_logs:
          module: dagster_aws.s3.compute_log_manager
          class: S3ComputeLogManager
          config:
            bucket: "mycorp-dagster-compute-logs"
            local_dir: "/tmp/cool"
            prefix: "dagster-test-"
            use_ssl: true
            verify: true
            verify_cert_path: "/path/to/cert/bundle.pem"
            endpoint_url: "http://alternate-s3-host.io"
            skip_empty_files: true

    Args:
        bucket (str): The name of the s3 bucket to which to log.
        local_dir (Optional[str]): Path to the local directory in which to stage logs. Default:
            ``dagster._seven.get_system_temp_directory()``.
        prefix (Optional[str]): Prefix for the log file keys.
        use_ssl (Optional[bool]): Whether or not to use SSL. Default True.
        verify (Optional[bool]): Whether or not to verify SSL certificates. Default True.
        verify_cert_path (Optional[str]): A filename of the CA cert bundle to use. Only used if
            `verify` set to False.
        endpoint_url (Optional[str]): Override for the S3 endpoint url.
        skip_empty_files: (Optional[bool]): Skip upload of empty log files.
        inst_data (Optional[ConfigurableClassData]): Serializable representation of the compute
            log manager when newed up from config.
    """

    def __init__(
        self,
        bucket,
        local_dir=None,
        inst_data=None,
        prefix="dagster",
        use_ssl=True,
        verify=True,
        verify_cert_path=None,
        endpoint_url=None,
        skip_empty_files=False,
    ):
        _verify = False if not verify else verify_cert_path
        self._s3_session = boto3.resource(
            "s3", use_ssl=use_ssl, verify=_verify, endpoint_url=endpoint_url
        ).meta.client
        self._s3_bucket = check.str_param(bucket, "bucket")
        self._s3_prefix = check.str_param(prefix, "prefix")

        # proxy calls to local compute log manager (for subscriptions, etc)
        if not local_dir:
            local_dir = seven.get_system_temp_directory()

        self._local_manager = LocalComputeLogManager(local_dir)
        self._subscription_manager = S3ComputeLogSubscriptionManager(self)
        self._inst_data = check.opt_inst_param(inst_data, "inst_data", ConfigurableClassData)
        self._skip_empty_files = check.bool_param(skip_empty_files, "skip_empty_files")

    @property
    def inst_data(self):
        return self._inst_data

    @classmethod
    def config_type(cls):
        return {
            "bucket": StringSource,
            "local_dir": Field(StringSource, is_required=False),
            "prefix": Field(StringSource, is_required=False, default_value="dagster"),
            "use_ssl": Field(bool, is_required=False, default_value=True),
            "verify": Field(bool, is_required=False, default_value=True),
            "verify_cert_path": Field(StringSource, is_required=False),
            "endpoint_url": Field(StringSource, is_required=False),
            "skip_empty_files": Field(bool, is_required=False, default_value=False),
        }

    @staticmethod
    def from_config_value(inst_data, config_value):
        return S3ComputeLogManager(inst_data=inst_data, **config_value)

    @contextmanager
    def capture_logs(self, log_key: List[str]):
        with self._poll_for_local_upload(log_key, interval=10):
            with self._local_manager.capture_logs(log_key):
                yield
        self._upload_from_local(log_key, ComputeIOType.STDOUT)
        self._upload_from_local(log_key, ComputeIOType.STDERR)

    def is_capture_complete(self, log_key: List[str]):
        return self._local_manager.is_capture_complete(log_key)

    def get_log_data(
        self,
        log_key: List[str],
        cursor: str = None,
        max_bytes: int = None,
    ) -> CapturedLogData:
        if self._should_download(log_key, ComputeIOType.STDOUT):
            self._download_to_local(log_key, ComputeIOType.STDOUT)
        if self._should_download(log_key, ComputeIOType.STDERR):
            self._download_to_local(log_key, ComputeIOType.STDERR)
        return self._local_manager.get_log_data(log_key, cursor, max_bytes)

    def get_log_metadata(self, log_key: List[str]) -> CapturedLogMetadata:
        stdout_s3_key = self._s3_key(log_key, ComputeIOType.STDOUT)
        stderr_s3_key = self._s3_key(log_key, ComputeIOType.STDERR)
        stdout_download_url = None
        stderr_download_url = None
        if self.is_capture_complete(log_key):
            stdout_download_url = self._s3_session.generate_presigned_url(
                ClientMethod="get_object", Params={"Bucket": self._s3_bucket, "Key": stdout_s3_key}
            )
            stderr_download_url = self._s3_session.generate_presigned_url(
                ClientMethod="get_object", Params={"Bucket": self._s3_bucket, "Key": stderr_s3_key}
            )

        return CapturedLogMetadata(
            stdout_location=f"s3://{self._s3_bucket}/{stdout_s3_key}",
            stderr_location=f"s3://{self._s3_bucket}/{stderr_s3_key}",
            stdout_download_url=stdout_download_url,
            stderr_download_url=stderr_download_url,
        )

    def on_progress(self, log_key):
        # should be called at some interval, to be used for streaming upload implementations
        if self.is_capture_complete(log_key):
            return

        self._upload_from_local(log_key, ComputeIOType.STDOUT, partial=True)
        self._upload_from_local(log_key, ComputeIOType.STDERR, partial=True)

    def subscribe(
        self, log_key: List[str], cursor: Optional[str] = None
    ) -> CapturedLogSubscription:
        subscription = CapturedLogSubscription(self, log_key, cursor)
        self.on_subscribe(subscription)
        return subscription

    def unsubscribe(self, subscription):
        self.on_unsubscribe(subscription)

    def get_in_progress_log_keys(self, prefix: Optional[List[str]] = None) -> List[List[str]]:
        return self._local_manager.get_in_progress_log_keys(prefix)

    def _has_local_file(self, log_key, io_type):
        local_path = self._local_manager._get_captured_local_path(
            log_key, IO_TYPE_EXTENSION[io_type]
        )
        return os.path.exists(local_path)

    def _has_remote_file(self, log_key, io_type, partial=False):
        s3_key = self._s3_key(log_key, io_type, partial=partial)
        try:  # https://stackoverflow.com/a/38376288/14656695
            self._s3_session.head_object(Bucket=self._s3_bucket, Key=s3_key)
        except ClientError:
            return False
        return True

    def _should_download(self, log_key, io_type):
        return not self._has_local_file(log_key, io_type) and self._has_remote_file(
            log_key, io_type
        )

    def _upload_from_local(self, log_key, io_type, partial=False):
        path = self._local_manager._get_captured_local_path(log_key, IO_TYPE_EXTENSION[io_type])
        ensure_file(path)

        if (self._skip_empty_files or partial) and os.stat(path).st_size == 0:
            return

        s3_key = self._s3_key(log_key, io_type, partial=partial)
        with open(path, "rb") as data:
            self._s3_session.upload_fileobj(data, self._s3_bucket, s3_key)

    def _download_to_local(self, log_key, io_type, partial=False):
        path = self._local_manager._get_captured_local_path(
            log_key, IO_TYPE_EXTENSION[io_type], partial=partial
        )
        ensure_dir(os.path.dirname(path))
        s3_key = self._s3_key(log_key, io_type, partial=partial)
        with open(path, "wb") as fileobj:
            self._s3_session.download_fileobj(self._s3_bucket, s3_key, fileobj)

    def _s3_key(self, log_key, io_type, partial=False):
        check.inst_param(io_type, "io_type", ComputeIOType)
        extension = IO_TYPE_EXTENSION[io_type]
        [*namespace, filebase] = log_key
        filename = f"{filebase}.{extension}"
        if partial:
            filename = f"{filename}.partial"
        paths = [self._s3_prefix, "storage", *namespace, filename]
        return "/".join(paths)  # s3 path delimiter

    @contextmanager
    def _poll_for_local_upload(self, log_key, interval=10):
        poll_file = os.path.abspath(poll_upload.__file__)
        try:
            upload_process = open_ipc_subprocess(
                [
                    sys.executable,
                    poll_file,
                    str(os.getpid()),
                    serialize_dagster_namedtuple(self.inst_data),
                    json.dumps(log_key),
                    str(interval),
                ]
            )
            yield
        finally:
            if upload_process:
                interrupt_ipc_subprocess(upload_process)
                wait_for_process(upload_process)

    ###############################################
    #
    # Methods for the ComputeLogManager interface
    #
    ###############################################
    @contextmanager
    def _watch_logs(self, pipeline_run, step_key=None):
        # proxy watching to the local compute log manager, interacting with the filesystem
        log_key = self._local_manager.build_log_key_for_run(
            pipeline_run.run_id, step_key or pipeline_run.pipeline_name
        )
        with self._local_manager.capture_logs(log_key):
            yield
        self._upload_from_local(log_key, ComputeIOType.STDOUT)
        self._upload_from_local(log_key, ComputeIOType.STDERR)

    def get_local_path(self, run_id, key, io_type):
        return self._local_manager.get_local_path(run_id, key, io_type)

    def on_watch_start(self, pipeline_run, step_key):
        self._local_manager.on_watch_start(pipeline_run, step_key)

    def on_watch_finish(self, pipeline_run, step_key):
        self._local_manager.on_watch_finish(pipeline_run, step_key)

    def is_watch_completed(self, run_id, key):
        return self._local_manager.is_watch_completed(run_id, key) or self._has_remote_file(
            self._local_manager.build_log_key_for_run(run_id, key), ComputeIOType.STDERR
        )

    def download_url(self, run_id, key, io_type):
        if not self.is_watch_completed(run_id, key):
            return self._local_manager.download_url(run_id, key, io_type)

        log_key = self._local_manager.build_log_key_for_run(run_id, key)
        s3_key = self._s3_key(log_key, io_type)

        url = self._s3_session.generate_presigned_url(
            ClientMethod="get_object", Params={"Bucket": self._s3_bucket, "Key": s3_key}
        )

        return url

    def read_logs_file(self, run_id, key, io_type, cursor=0, max_bytes=MAX_BYTES_FILE_READ):
        log_key = self._local_manager.build_log_key_for_run(run_id, key)

        if self._has_local_file(log_key, io_type):
            data = self._local_manager.read_logs_file(run_id, key, io_type, cursor, max_bytes)
            return self._from_local_file_data(run_id, key, io_type, data)
        elif self._has_remote_file(log_key, io_type):
            self._download_to_local(log_key, io_type)
            data = self._local_manager.read_logs_file(run_id, key, io_type, cursor, max_bytes)
            return self._from_local_file_data(run_id, key, io_type, data)
        elif self._has_remote_file(log_key, io_type, partial=True):
            self._download_to_local(log_key, io_type, partial=True)
            partial_path = self._local_manager._get_captured_local_path(
                log_key, IO_TYPE_EXTENSION[io_type], partial=True
            )
            captured_data, new_cursor = self._local_manager._read_path(partial_path, offset=cursor)
            return ComputeLogFileData(
                path=partial_path,
                data=captured_data.decode("utf-8") if captured_data else None,
                cursor=new_cursor or 0,
                size=len(captured_data) if captured_data else 0,
                download_url=None,
            )
        local_path = self._local_manager._get_captured_local_path(
            log_key, IO_TYPE_EXTENSION[io_type]
        )
        return ComputeLogFileData(path=local_path, data=None, cursor=0, size=0, download_url=None)

    def on_subscribe(self, subscription):
        self._subscription_manager.add_subscription(subscription)

    def on_unsubscribe(self, subscription):
        self._subscription_manager.remove_subscription(subscription)

    def dispose(self):
        self._subscription_manager.dispose()

    def _from_local_file_data(self, run_id, key, io_type, local_file_data):
        log_key = self._local_manager.build_log_key_for_run(run_id, key)
        s3_key = self._s3_key(log_key, ComputeIOType.STDOUT)

        return ComputeLogFileData(
            f"s3://{self._s3_bucket}/{s3_key}",
            local_file_data.data,
            local_file_data.cursor,
            local_file_data.size,
            self.download_url(run_id, key, io_type),
        )

    def dispose(self):
        self._local_manager.dispose()


class S3ComputeLogSubscriptionManager:
    def __init__(self, manager):
        self._manager = manager
        self._subscriptions = defaultdict(list)
        self.__polling_thread = threading.Thread(
            target=self._poll,
            name="s3-compute-log-streaming",
        )
        self.__shutdown_event = threading.Event()
        self.__polling_thread.daemon = True
        self.__polling_thread.start()

    def _watch_key(self, log_key: List[str]) -> str:
        return json.dumps(log_key)

    def add_subscription(
        self, subscription: Union[ComputeLogSubscription, CapturedLogSubscription]
    ):
        check.inst_param(
            subscription, "subscription", (ComputeLogSubscription, CapturedLogSubscription)
        )

        if self.is_complete(subscription):
            subscription.fetch()
            subscription.complete()
        else:
            log_key = self._log_key(subscription)
            watch_key = self._watch_key(log_key)
            self._subscriptions[watch_key].append(subscription)

    def remove_subscription(self, subscription):
        check.inst_param(subscription, "subscription", ComputeLogSubscription)
        watch_key = self._watch_key(subscription.run_id, subscription.key)
        if subscription in self._subscriptions[watch_key]:
            self._subscriptions[watch_key].remove(subscription)
            subscription.complete()

    def remove_all_subscriptions(self, log_key):
        watch_key = self._watch_key(log_key)
        for subscription in self._subscriptions.pop(watch_key, []):
            subscription.complete()

    def notify_subscriptions(self, log_key):
        watch_key = self._watch_key(log_key)
        for subscription in self._subscriptions[watch_key]:
            subscription.fetch()

    def _poll(self):
        while True:
            if self.__shutdown_event.is_set():
                return
            # need to do something smarter here that keeps track of updates
            for _, subscriptions in self._subscriptions.items():
                for subscription in subscriptions:
                    if self.__shutdown_event.is_set():
                        return
                    subscription.fetch()
            time.sleep(POLLING_INTERVAL)

    def dispose(self):
        self.__shutdown_event.set()
