from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import List, NamedTuple, Optional

from dagster._core.instance import MayHaveInstanceWeakref


class CapturedLogData(
    NamedTuple(
        "_CapturedLogData",
        [("chunk", Optional[bytes]), ("cursor", Optional[int])],
    )
):
    """
    Object representing captured log data, either a partial chunk of the log data or the full
    capture.  Contains the raw bytes and optionally the cursor offset for the partial chunk.
    """

    def __new__(cls, chunk: Optional[bytes] = None, cursor: Optional[int] = None):
        return super(CapturedLogData, cls).__new__(cls, chunk, cursor)


class CapturedLogMetadata(
    NamedTuple(
        "_CapturedLogMetadata",
        [("location", Optional[str]), ("download_url", Optional[str])],
    )
):
    """
    Object representing metadata info for the captured log data, containing a display string for
    the location of the log data and a URL for direct download of the captured log data.
    """

    def __new__(cls, location: Optional[str] = None, download_url: Optional[str] = None):
        return super(CapturedLogMetadata, cls).__new__(cls, location, download_url)


class CapturedLogManager(ABC, MayHaveInstanceWeakref):
    """Abstract base class for capturing the unstructured logs (stdout/stderr) in the current
    process, stored / retrieved with a provided log_key."""

    @abstractmethod
    @contextmanager
    def capture_logs(self, log_key: List[str]):
        """
        Context manager for capturing the stdout/stderr within the current process, and persisting
        it under the given log key.

        Args:
            log_key (List[String]): The log key identifying the captured logs
        """

    @abstractmethod
    def is_capture_complete(self, log_key: List[str]):
        """Flag indicating when the log capture for a given log key has completed.

        Args:
            log_key (List[String]): The log key identifying the captured logs

        Returns:
            Boolean
        """

    @abstractmethod
    def get_stdout(
        self,
        log_key: List[str],
        cursor: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ) -> CapturedLogData:
        """Returns a chunk of the captured stdout logs for a given log key

        Args:
            log_key (List[String]): The log key identifying the captured logs
            cursor (Optional[str]): A cursor representing the position of the log chunk to fetch
            max_bytes (Optional[int]): A limit on the size of the log chunk to fetch

        Returns:
            CapturedLogData
        """

    @abstractmethod
    def get_stderr(
        self,
        log_key: List[str],
        cursor: str = None,
        max_bytes: int = None,
    ) -> CapturedLogData:
        """Returns a chunk of the captured stderr logs for a given log key

        Args:
            log_key (List[String]): The log key identifying the captured logs
            cursor (Optional[str]): A cursor representing the position of the log chunk to fetch
            max_bytes (Optional[int]): A limit on the size of the log chunk to fetch

        Returns:
            CapturedLogData
        """

    @abstractmethod
    def get_stdout_metadata(self, log_key: List[str]) -> CapturedLogMetadata:
        """Returns the metadata of the captured stdout logs for a given log key, including
        displayable information on where the logs are persisted.

        Args:
            log_key (List[String]): The log key identifying the captured logs

        Returns:
            CapturedLogMetadata
        """

    @abstractmethod
    def get_stderr_metadata(self, log_key: List[str]) -> CapturedLogMetadata:
        """Returns the metadata of the captured stderr logs for a given log key, including
        displayable information on where the logs are persisted.

        Args:
            log_key (List[String]): The log key identifying the captured logs

        Returns:
            CapturedLogMetadata
        """
