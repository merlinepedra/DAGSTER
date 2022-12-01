import hashlib
import json
import re
from datetime import datetime
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Union,
    cast,
)

import pendulum

import dagster._check as check
from dagster._annotations import PublicAttr, public
from dagster._utils.partitions import DEFAULT_HOURLY_FORMAT_WITHOUT_TIMEZONE
from dagster._utils.schedules import cron_string_iterator, reverse_cron_string_iterator

from .partition import (
    DEFAULT_DATE_FORMAT,
    Partition,
    PartitionedConfig,
    PartitionsDefinition,
    PartitionsSubset,
    ScheduleType,
    cron_schedule_from_schedule_type_and_offsets,
)
from .partition_key_range import PartitionKeyRange


class TimeWindow(NamedTuple):
    """An interval that is closed at the start and open at the end.

    Attributes:
        start (datetime): A pendulum datetime that marks the start of the window.
        end (datetime): A pendulum datetime that marks the end of the window.
    """

    start: PublicAttr[datetime]
    end: PublicAttr[datetime]


class TimeWindowPartitionsDefinition(
    PartitionsDefinition[TimeWindow],  # pylint: disable=unsubscriptable-object
    NamedTuple(
        "_TimeWindowPartitionsDefinition",
        [
            ("start", PublicAttr[datetime]),
            ("timezone", PublicAttr[str]),
            ("fmt", PublicAttr[str]),
            ("end_offset", PublicAttr[int]),
            ("cron_schedule", PublicAttr[str]),
        ],
    ),
):
    """
    A set of partitions where each partitions corresponds to a time window.

    The provided cron_schedule determines the bounds of the time windows. E.g. a cron_schedule of
    "0 0 \\* \\* \\*" will result in daily partitions that start at midnight and end at midnight of the
    following day.

    The string partition_key associated with each partition corresponds to the start of the
    partition's time window.

    The first partition in the set will start on at the first cron_schedule tick that is equal to
    or after the given start datetime. The last partition in the set will end before the current
    time, unless the end_offset argument is set to a positive number.

    Args:
        cron_schedule (str): Determines the bounds of the time windows.
        start (datetime): The first partition in the set will start on at the first cron_schedule
            tick that is equal to or after this value.
        timezone (Optional[str]): The timezone in which each time should exist.
            Supported strings for timezones are the ones provided by the
            `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
        fmt (str): The date format to use for partition_keys.
        end_offset (int): Extends the partition set by a number of partitions equal to the value
            passed. If end_offset is 0 (the default), the last partition ends before the current
            time. If end_offset is 1, the second-to-last partition ends before the current time,
            and so on.
    """

    def __new__(  # pylint: disable=arguments-differ
        cls,
        start: Union[datetime, str],
        fmt: str,
        schedule_type: Optional[ScheduleType] = None,
        timezone: Optional[str] = None,
        end_offset: int = 0,
        minute_offset: Optional[int] = None,
        hour_offset: Optional[int] = None,
        day_offset: Optional[int] = None,
        cron_schedule: Optional[str] = None,
    ):
        if isinstance(start, datetime):
            start_dt = start
        else:
            start_dt = datetime.strptime(start, fmt)

        if cron_schedule is not None:
            check.invariant(
                schedule_type is None and not minute_offset and not hour_offset and not day_offset,
                "If cron_schedule argument is provided, then schedule_type, minute_offset, "
                "hour_offset, and day_offset can't also be provided",
            )
        else:
            if schedule_type is None:
                check.failed("One of schedule_type and cron_schedule must be provided")

            cron_schedule = cron_schedule_from_schedule_type_and_offsets(
                schedule_type=schedule_type,
                minute_offset=minute_offset or 0,
                hour_offset=hour_offset or 0,
                day_offset=day_offset or 0,
            )

        return super(TimeWindowPartitionsDefinition, cls).__new__(
            cls, start_dt, timezone or "UTC", fmt, end_offset, cron_schedule
        )

    def get_partitions(
        self, current_time: Optional[datetime] = None
    ) -> Sequence[Partition[TimeWindow]]:
        current_timestamp = (
            pendulum.instance(current_time, tz=self.timezone)
            if current_time
            else pendulum.now(self.timezone)
        ).timestamp()

        partitions_past_current_time = 0
        partitions: List[Partition[TimeWindow]] = []
        for time_window in self._iterate_time_windows(self.start):
            if (
                time_window.end.timestamp() <= current_timestamp
                or partitions_past_current_time < self.end_offset
            ):
                partitions.append(
                    Partition(value=time_window, name=time_window.start.strftime(self.fmt))
                )

                if time_window.end.timestamp() > current_timestamp:
                    partitions_past_current_time += 1
            else:
                break

        if self.end_offset < 0:
            partitions = partitions[: self.end_offset]

        return partitions

    def __str__(self) -> str:
        schedule_str = (
            self.schedule_type.value.capitalize() if self.schedule_type else self.cron_schedule
        )
        partition_def_str = (
            f"{schedule_str}, starting {self.start.strftime(self.fmt)} {self.timezone}."
        )
        if self.end_offset != 0:
            partition_def_str += f" End offsetted by {self.end_offset} partition{'' if self.end_offset == 1 else 's'}."
        return partition_def_str

    def __eq__(self, other):
        return (
            isinstance(other, TimeWindowPartitionsDefinition)
            and pendulum.instance(self.start, tz=self.timezone).timestamp()
            == pendulum.instance(other.start, tz=other.timezone).timestamp()
            and self.timezone == other.timezone
            and self.fmt == other.fmt
            and self.end_offset == other.end_offset
            and self.cron_schedule == other.cron_schedule
        )

    def __hash__(self):
        return hash(tuple(self.__repr__()))

    def time_window_for_partition_key(self, partition_key: str) -> TimeWindow:
        partition_key_dt = pendulum.instance(
            datetime.strptime(partition_key, self.fmt), tz=self.timezone
        )
        return next(iter(self._iterate_time_windows(partition_key_dt)))

    def start_time_for_partition_key(self, partition_key: str) -> datetime:
        partition_key_dt = pendulum.instance(
            datetime.strptime(partition_key, self.fmt), tz=self.timezone
        )
        # the datetime format might not include granular components, so we need to recover them
        # we make the assumption that the parsed partition key is <= the start datetime
        return next(iter(self._iterate_time_windows(partition_key_dt))).start

    def get_next_partition_key(
        self, partition_key: str, current_time: Optional[datetime] = None
    ) -> Optional[str]:
        partition_key_dt = pendulum.instance(
            datetime.strptime(partition_key, self.fmt), tz=self.timezone
        )
        windows_iter = iter(self._iterate_time_windows(partition_key_dt))
        next(windows_iter)
        start_time = next(windows_iter).start
        if start_time >= self.get_last_partition_window(current_time).end:
            return None
        else:
            return start_time.strftime(self.fmt)

    def get_last_partition_window(self, current_time: Optional[datetime] = None) -> TimeWindow:
        current_time = (
            pendulum.instance(current_time, tz=self.timezone)
            if current_time
            else pendulum.now(self.timezone)
        )

        if self.end_offset == 0:
            return next(iter(self._reverse_iterate_time_windows(current_time)))
        else:
            # TODO: make this efficient
            return self.time_window_for_partition_key(super().get_last_partition_key(current_time))

    def get_last_partition_key(self, current_time: Optional[datetime] = None) -> str:
        return self.get_last_partition_window(current_time).start.strftime(self.fmt)

    def end_time_for_partition_key(self, partition_key: str) -> datetime:
        return self.time_window_for_partition_key(partition_key).end

    def get_default_partition_mapping(self):
        from dagster._core.definitions.time_window_partition_mapping import (
            TimeWindowPartitionMapping,
        )

        return TimeWindowPartitionMapping()

    def get_partition_keys_in_range(self, partition_key_range: PartitionKeyRange) -> Sequence[str]:
        start_time = self.start_time_for_partition_key(partition_key_range.start)
        end_time = self.start_time_for_partition_key(partition_key_range.end)

        result: List[str] = []
        for time_window in self._iterate_time_windows(start_time):
            if time_window.start <= end_time:
                result.append(time_window.start.strftime(self.fmt))
            else:
                break

        current_partitions = [partition.name for partition in self.get_partitions()]
        return [partition for partition in result if partition in current_partitions]

    @public  # type: ignore
    @property
    def schedule_type(self) -> Optional[ScheduleType]:
        if re.match(r"\d+ \* \* \* \*", self.cron_schedule):
            return ScheduleType.HOURLY
        elif re.match(r"\d+ \d+ \* \* \*", self.cron_schedule):
            return ScheduleType.DAILY
        elif re.match(r"\d+ \d+ \* \* \d+", self.cron_schedule):
            return ScheduleType.WEEKLY
        elif re.match(r"\d+ \d+ \d+ \* \*", self.cron_schedule):
            return ScheduleType.MONTHLY
        else:
            return None

    @public  # type: ignore
    @property
    def minute_offset(self) -> int:
        match = re.match(r"(\d+) (\d+|\*) (\d+|\*) (\d+|\*) (\d+|\*)", self.cron_schedule)
        if match is None:
            check.failed(f"{self.cron_schedule} has no minute offset")
        return int(match.groups()[0])

    @public  # type: ignore
    @property
    def hour_offset(self) -> int:
        match = re.match(r"(\d+|\*) (\d+) (\d+|\*) (\d+|\*) (\d+|\*)", self.cron_schedule)
        if match is None:
            check.failed(f"{self.cron_schedule} has no hour offset")
        return int(match.groups()[1])

    @public  # type: ignore
    @property
    def day_offset(self) -> int:
        schedule_type = self.schedule_type
        if schedule_type == ScheduleType.WEEKLY:
            match = re.match(r"(\d+|\*) (\d+|\*) (\d+|\*) (\d+|\*) (\d+)", self.cron_schedule)
            if match is None:
                check.failed(f"{self.cron_schedule} has no day offset")
            return int(match.groups()[4])
        elif schedule_type == ScheduleType.MONTHLY:
            match = re.match(r"(\d+|\*) (\d+|\*) (\d+) (\d+|\*) (\d+|\*)", self.cron_schedule)
            if match is None:
                check.failed(f"{self.cron_schedule} has no day offset")
            return int(match.groups()[2])
        else:
            check.failed(f"Unsupported schedule type for day_offset: {schedule_type}")

    @public
    def get_cron_schedule(
        self,
        minute_of_hour: Optional[int] = None,
        hour_of_day: Optional[int] = None,
        day_of_week: Optional[int] = None,
        day_of_month: Optional[int] = None,
    ) -> str:
        """The schedule executes at the cadence specified by the partitioning, but may overwrite
        the minute/hour/day offset of the partitioning.

        This is useful e.g. if you have partitions that span midnight to midnight but you want to
        schedule a job that runs at 2 am."""
        if (
            minute_of_hour is None
            and hour_of_day is None
            and day_of_week is None
            and day_of_month is None
        ):
            return self.cron_schedule

        schedule_type = self.schedule_type
        if schedule_type is None:
            check.failed(
                f"{self.cron_schedule} does not support minute_of_hour/hour_of_day/day_of_week/day_of_month arguments"
            )

        minute_of_hour = cast(
            int,
            check.opt_int_param(minute_of_hour, "minute_of_hour", default=self.minute_offset),
        )

        if schedule_type == ScheduleType.HOURLY:
            check.invariant(
                hour_of_day is None, "Cannot set hour parameter with hourly partitions."
            )
        else:
            hour_of_day = cast(
                int, check.opt_int_param(hour_of_day, "hour_of_day", default=self.hour_offset)
            )

        if schedule_type == ScheduleType.DAILY:
            check.invariant(
                day_of_week is None, "Cannot set day of week parameter with daily partitions."
            )
            check.invariant(
                day_of_month is None, "Cannot set day of month parameter with daily partitions."
            )

        if schedule_type == ScheduleType.MONTHLY:
            default = self.day_offset or 1
            day_offset = check.opt_int_param(day_of_month, "day_of_month", default=default)
        elif schedule_type == ScheduleType.WEEKLY:
            default = self.day_offset or 0
            day_offset = check.opt_int_param(day_of_week, "day_of_week", default=default)
        else:
            day_offset = 0

        return cron_schedule_from_schedule_type_and_offsets(
            schedule_type,
            minute_offset=minute_of_hour,
            hour_offset=hour_of_day or 0,
            day_offset=day_offset,
        )

    def _iterate_time_windows(self, start: datetime) -> Iterable[TimeWindow]:
        """
        Returns an infinite generator of time windows that start after the given start time.
        """
        start_timestamp = pendulum.instance(start, tz=self.timezone).timestamp()
        iterator = cron_string_iterator(
            start_timestamp=start_timestamp,
            cron_string=self.cron_schedule,
            execution_timezone=self.timezone,
        )

        prev_time = next(iterator)
        while prev_time.timestamp() < start_timestamp:
            prev_time = next(iterator)

        while True:
            next_time = next(iterator)
            yield TimeWindow(prev_time, next_time)
            prev_time = next_time

    def _reverse_iterate_time_windows(self, end: datetime) -> Iterable[TimeWindow]:
        """
        Returns an infinite generator of time windows that end before the given end time.
        """
        end_timestamp = pendulum.instance(end, tz=self.timezone).timestamp()
        iterator = reverse_cron_string_iterator(
            end_timestamp=end_timestamp,
            cron_string=self.cron_schedule,
            execution_timezone=self.timezone,
        )

        prev_time = next(iterator)
        while prev_time.timestamp() > end_timestamp:
            prev_time = next(iterator)

        while True:
            next_time = next(iterator)
            yield TimeWindow(next_time, prev_time)
            prev_time = next_time

    def get_partition_key_for_timestamp(self, timestamp: float, end_closed: bool = False) -> str:
        """
        Args:
            timestamp (float): Timestamp from the unix epoch, UTC.
            end_closed (bool): Whether the interval is closed at the end or at the beginning.
        """
        iterator = cron_string_iterator(
            timestamp, self.cron_schedule, self.timezone, start_offset=-1
        )
        # prev will be < timestamp
        prev = next(iterator)
        # prev_next will be >= timestamp
        prev_next = next(iterator)

        if end_closed or prev_next.timestamp() > timestamp:
            return prev.strftime(self.fmt)
        else:
            return prev_next.strftime(self.fmt)

    def less_than(self, partition_key1: str, partition_key2: str) -> bool:
        """Returns true if the partition_key1 is earlier than partition_key2"""
        return self.start_time_for_partition_key(
            partition_key1
        ) < self.start_time_for_partition_key(partition_key2)

    def empty_subset(self) -> "TimeWindowPartitionsSubset":
        return TimeWindowPartitionsSubset(self, [])

    def deserialize_subset(self, serialized: str) -> "TimeWindowPartitionsSubset":
        return TimeWindowPartitionsSubset.from_serialized(self, serialized)

    @property
    def serializable_unique_identifier(self) -> str:
        return hashlib.sha1(self.__repr__().encode("utf-8")).hexdigest()


class DailyPartitionsDefinition(TimeWindowPartitionsDefinition):
    def __new__(  # pylint: disable=signature-differs
        cls,
        start_date: Union[datetime, str],
        minute_offset: int = 0,
        hour_offset: int = 0,
        timezone: Optional[str] = None,
        fmt: Optional[str] = None,
        end_offset: int = 0,
    ):
        """A set of daily partitions.

        The first partition in the set will start at the start_date at midnight. The last partition
        in the set will end before the current time, unless the end_offset argument is set to a
        positive number. If minute_offset and/or hour_offset are used, the start and end times of
        each partition will be hour_offset:minute_offset of each day.

        Args:
            start_date (Union[datetime.datetime, str]): The first date in the set of partitions. Can
                provide in either a datetime or string format.
            minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
                to 0.
            hour_offset (int): Number of hours past 00:00 to "split" the partition. Defaults to 0.
            timezone (Optional[str]): The timezone in which each date should exist.
                Supported strings for timezones are the ones provided by the
                `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
            fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
            end_offset (int): Extends the partition set by a number of partitions equal to the value
                passed. If end_offset is 0 (the default), the last partition ends before the current
                time. If end_offset is 1, the second-to-last partition ends before the current time,
                and so on.

        .. code-block:: python
            DailyPartitionsDefinition(start_date="2022-03-12")
            # creates partitions (2022-03-12-00:00, 2022-03-13-00:00), (2022-03-13-00:00, 2022-03-14-00:00), ...

            DailyPartitionsDefinition(start_date="2022-03-12", minute_offset=15, hour_offset=16)
            # creates partitions (2022-03-12-16:15, 2022-03-13-16:15), (2022-03-13-16:15, 2022-03-14-16:15), ...
        """
        _fmt = fmt or DEFAULT_DATE_FORMAT

        return super(DailyPartitionsDefinition, cls).__new__(
            cls,
            schedule_type=ScheduleType.DAILY,
            start=start_date,
            minute_offset=minute_offset,
            hour_offset=hour_offset,
            timezone=timezone,
            fmt=_fmt,
            end_offset=end_offset,
        )


def wrap_time_window_tags_fn(
    tags_fn: Optional[Callable[[datetime, datetime], Mapping[str, str]]]
) -> Callable[[Partition], Mapping[str, str]]:
    def _tag_wrapper(partition: Partition) -> Mapping[str, str]:
        if not tags_fn:
            return {}
        return tags_fn(cast(datetime, partition.value[0]), cast(datetime, partition.value[1]))

    return _tag_wrapper


def daily_partitioned_config(
    start_date: Union[datetime, str],
    minute_offset: int = 0,
    hour_offset: int = 0,
    timezone: Optional[str] = None,
    fmt: Optional[str] = None,
    end_offset: int = 0,
    tags_for_partition_fn: Optional[Callable[[datetime, datetime], Mapping[str, str]]] = None,
) -> Callable[[Callable[[datetime, datetime], Mapping[str, Any]]], PartitionedConfig]:
    """Defines run config over a set of daily partitions.

    The decorated function should accept a start datetime and end datetime, which represent the bounds
    of the date partition the config should delineate.

    The decorated function should return a run config dictionary.

    The resulting object created by this decorator can be provided to the config argument of a Job.
    The first partition in the set will start at the start_date at midnight. The last partition in
    the set will end before the current time, unless the end_offset argument is set to a positive
    number. If minute_offset and/or hour_offset are used, the start and end times of each partition
    will be hour_offset:minute_offset of each day.

    Args:
        start_date (Union[datetime.datetime, str]): The first date in the set of partitions. Can
            provide in either a datetime or string format.
        minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
            to 0.
        hour_offset (int): Number of hours past 00:00 to "split" the partition. Defaults to 0.
        timezone (Optional[str]): The timezone in which each date should exist.
            Supported strings for timezones are the ones provided by the
            `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
        fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
        end_offset (int): Extends the partition set by a number of partitions equal to the value
            passed. If end_offset is 0 (the default), the last partition ends before the current
            time. If end_offset is 1, the second-to-last partition ends before the current time,
            and so on.

    .. code-block:: python

        @daily_partitioned_config(start_date="2022-03-12")
        # creates partitions (2022-03-12-00:00, 2022-03-13-00:00), (2022-03-13-00:00, 2022-03-14-00:00), ...

        @daily_partitioned_config(start_date="2022-03-12", minute_offset=15, hour_offset=16)
        # creates partitions (2022-03-12-16:15, 2022-03-13-16:15), (2022-03-13-16:15, 2022-03-14-16:15), ...
    """

    def inner(fn: Callable[[datetime, datetime], Mapping[str, Any]]) -> PartitionedConfig:
        check.callable_param(fn, "fn")

        return PartitionedConfig(
            run_config_for_partition_fn=lambda partition: fn(
                partition.value[0], partition.value[1]
            ),
            partitions_def=DailyPartitionsDefinition(
                start_date=start_date,
                minute_offset=minute_offset,
                hour_offset=hour_offset,
                timezone=timezone,
                fmt=fmt,
                end_offset=end_offset,
            ),
            decorated_fn=fn,
            tags_for_partition_fn=wrap_time_window_tags_fn(tags_for_partition_fn),
        )

    return inner


class HourlyPartitionsDefinition(TimeWindowPartitionsDefinition):
    def __new__(  # pylint: disable=signature-differs
        cls,
        start_date: Union[datetime, str],
        minute_offset: int = 0,
        timezone: Optional[str] = None,
        fmt: Optional[str] = None,
        end_offset: int = 0,
    ):
        """A set of hourly partitions.

        The first partition in the set will start on the start_date at midnight. The last partition
        in the set will end before the current time, unless the end_offset argument is set to a
        positive number. If minute_offset is provided, the start and end times of each partition
        will be minute_offset past the hour.

        Args:
            start_date (Union[datetime.datetime, str]): The first date in the set of partitions. Can
                provide in either a datetime or string format.
            minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
                to 0.
            fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
            timezone (Optional[str]): The timezone in which each date should exist.
                Supported strings for timezones are the ones provided by the
                `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
            end_offset (int): Extends the partition set by a number of partitions equal to the value
                passed. If end_offset is 0 (the default), the last partition ends before the current
                time. If end_offset is 1, the second-to-last partition ends before the current time,
                and so on.

        .. code-block:: python

            HourlyPartitionsDefinition(start_date=datetime(2022, 03, 12))
            # creates partitions (2022-03-12-00:00, 2022-03-12-01:00), (2022-03-12-01:00, 2022-03-12-02:00), ...

            HourlyPartitionsDefinition(start_date=datetime(2022, 03, 12), minute_offset=15)
            # creates partitions (2022-03-12-00:15, 2022-03-12-01:15), (2022-03-12-01:15, 2022-03-12-02:15), ...
        """
        _fmt = fmt or DEFAULT_HOURLY_FORMAT_WITHOUT_TIMEZONE

        return super(HourlyPartitionsDefinition, cls).__new__(
            cls,
            schedule_type=ScheduleType.HOURLY,
            start=start_date,
            minute_offset=minute_offset,
            timezone=timezone,
            fmt=_fmt,
            end_offset=end_offset,
        )


def hourly_partitioned_config(
    start_date: Union[datetime, str],
    minute_offset: int = 0,
    timezone: Optional[str] = None,
    fmt: Optional[str] = None,
    end_offset: int = 0,
    tags_for_partition_fn: Optional[Callable[[datetime, datetime], Mapping[str, str]]] = None,
) -> Callable[[Callable[[datetime, datetime], Mapping[str, Any]]], PartitionedConfig]:
    """Defines run config over a set of hourly partitions.

    The decorated function should accept a start datetime and end datetime, which represent the date
    partition the config should delineate.

    The decorated function should return a run config dictionary.

    The resulting object created by this decorator can be provided to the config argument of a Job.
    The first partition in the set will start at the start_date at midnight. The last partition in
    the set will end before the current time, unless the end_offset argument is set to a positive
    number. If minute_offset is provided, the start and end times of each partition will be
    minute_offset past the hour.

    Args:
        start_date (Union[datetime.datetime, str]): The first date in the set of partitions. Can
            provide in either a datetime or string format.
        minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
            to 0.
        fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
        timezone (Optional[str]): The timezone in which each date should exist.
            Supported strings for timezones are the ones provided by the
            `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
        end_offset (int): Extends the partition set by a number of partitions equal to the value
            passed. If end_offset is 0 (the default), the last partition ends before the current
            time. If end_offset is 1, the second-to-last partition ends before the current time,
            and so on.

    .. code-block:: python

        @hourly_partitioned_config(start_date=datetime(2022, 03, 12))
        # creates partitions (2022-03-12-00:00, 2022-03-12-01:00), (2022-03-12-01:00, 2022-03-12-02:00), ...

        @hourly_partitioned_config(start_date=datetime(2022, 03, 12), minute_offset=15)
        # creates partitions (2022-03-12-00:15, 2022-03-12-01:15), (2022-03-12-01:15, 2022-03-12-02:15), ...
    """

    def inner(fn: Callable[[datetime, datetime], Mapping[str, Any]]) -> PartitionedConfig:
        check.callable_param(fn, "fn")

        return PartitionedConfig(
            run_config_for_partition_fn=lambda partition: fn(
                partition.value[0], partition.value[1]
            ),
            partitions_def=HourlyPartitionsDefinition(
                start_date=start_date,
                minute_offset=minute_offset,
                timezone=timezone,
                fmt=fmt,
                end_offset=end_offset,
            ),
            decorated_fn=fn,
            tags_for_partition_fn=wrap_time_window_tags_fn(tags_for_partition_fn),
        )

    return inner


class MonthlyPartitionsDefinition(TimeWindowPartitionsDefinition):
    def __new__(  # pylint: disable=signature-differs
        cls,
        start_date: Union[datetime, str],
        minute_offset: int = 0,
        hour_offset: int = 0,
        day_offset: int = 1,
        timezone: Optional[str] = None,
        fmt: Optional[str] = None,
        end_offset: int = 0,
    ):
        """A set of monthly partitions.

        The first partition in the set will start at the soonest first of the month after start_date
        at midnight. The last partition in the set will end before the current time, unless the
        end_offset argument is set to a positive number. If day_offset is provided, the start and
        end date of each partition will be day_offset. If minute_offset and/or hour_offset are used,
        the start and end times of each partition will be hour_offset:minute_offset of each day.

        Args:
            start_date (Union[datetime.datetime, str]): The first date in the set of partitions will be
                midnight the sonnest first of the month following start_date. Can provide in either a
                datetime or string format.
            minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
                to 0.
            hour_offset (int): Number of hours past 00:00 to "split" the partition. Defaults to 0.
            day_offset (int): Day of the month to "split" the partition. Defaults to 1.
            timezone (Optional[str]): The timezone in which each date should exist.
                Supported strings for timezones are the ones provided by the
                `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
            fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
            end_offset (int): Extends the partition set by a number of partitions equal to the value
                passed. If end_offset is 0 (the default), the last partition ends before the current
                time. If end_offset is 1, the second-to-last partition ends before the current time,
                and so on.

        .. code-block:: python

            MonthlyPartitionsDefinition(start_date="2022-03-12")
            # creates partitions (2022-04-01-00:00, 2022-05-01-00:00), (2022-05-01-00:00, 2022-06-01-00:00), ...

            MonthlyPartitionsDefinition(start_date="2022-03-12", minute_offset=15, hour_offset=3, day_offset=5)
            # creates partitions (2022-04-05-03:15, 2022-05-05-03:15), (2022-05-05-03:15, 2022-06-05-03:15), ...
        """
        _fmt = fmt or DEFAULT_DATE_FORMAT

        return super(MonthlyPartitionsDefinition, cls).__new__(
            cls,
            schedule_type=ScheduleType.MONTHLY,
            start=start_date,
            minute_offset=minute_offset,
            hour_offset=hour_offset,
            day_offset=day_offset,
            timezone=timezone,
            fmt=_fmt,
            end_offset=end_offset,
        )


def monthly_partitioned_config(
    start_date: Union[datetime, str],
    minute_offset: int = 0,
    hour_offset: int = 0,
    day_offset: int = 1,
    timezone: Optional[str] = None,
    fmt: Optional[str] = None,
    end_offset: int = 0,
    tags_for_partition_fn: Optional[Callable[[datetime, datetime], Mapping[str, str]]] = None,
) -> Callable[[Callable[[datetime, datetime], Mapping[str, Any]]], PartitionedConfig]:
    """Defines run config over a set of monthly partitions.

    The decorated function should accept a start datetime and end datetime, which represent the date
    partition the config should delineate.

    The decorated function should return a run config dictionary.

    The resulting object created by this decorator can be provided to the config argument of a Job.
    The first partition in the set will start at midnight on the soonest first of the month after
    start_date. The last partition in the set will end before the current time, unless the
    end_offset argument is set to a positive number. If day_offset is provided, the start and end
    date of each partition will be day_offset. If minute_offset and/or hour_offset are used, the
    start and end times of each partition will be hour_offset:minute_offset of each day.

    Args:
        start_date (Union[datetime.datetime, str]): The first date in the set of partitions will be
            midnight the sonnest first of the month following start_date. Can provide in either a
            datetime or string format.
        minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
            to 0.
        hour_offset (int): Number of hours past 00:00 to "split" the partition. Defaults to 0.
        day_offset (int): Day of the month to "split" the partition. Defaults to 1.
        timezone (Optional[str]): The timezone in which each date should exist.
            Supported strings for timezones are the ones provided by the
            `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
        fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
        end_offset (int): Extends the partition set by a number of partitions equal to the value
            passed. If end_offset is 0 (the default), the last partition ends before the current
            time. If end_offset is 1, the second-to-last partition ends before the current time,
            and so on.

    .. code-block:: python

        @monthly_partitioned_config(start_date="2022-03-12")
        # creates partitions (2022-04-01-00:00, 2022-05-01-00:00), (2022-05-01-00:00, 2022-06-01-00:00), ...

        @monthly_partitioned_config(start_date="2022-03-12", minute_offset=15, hour_offset=3, day_offset=5)
        # creates partitions (2022-04-05-03:15, 2022-05-05-03:15), (2022-05-05-03:15, 2022-06-05-03:15), ...
    """

    def inner(fn: Callable[[datetime, datetime], Mapping[str, Any]]) -> PartitionedConfig:
        check.callable_param(fn, "fn")

        return PartitionedConfig(
            run_config_for_partition_fn=lambda partition: fn(
                partition.value[0], partition.value[1]
            ),
            partitions_def=MonthlyPartitionsDefinition(
                start_date=start_date,
                minute_offset=minute_offset,
                hour_offset=hour_offset,
                day_offset=day_offset,
                timezone=timezone,
                fmt=fmt,
                end_offset=end_offset,
            ),
            decorated_fn=fn,
            tags_for_partition_fn=wrap_time_window_tags_fn(tags_for_partition_fn),
        )

    return inner


class WeeklyPartitionsDefinition(TimeWindowPartitionsDefinition):
    def __new__(  # pylint: disable=signature-differs
        cls,
        start_date: Union[datetime, str],
        minute_offset: int = 0,
        hour_offset: int = 0,
        day_offset: int = 0,
        timezone: Optional[str] = None,
        fmt: Optional[str] = None,
        end_offset: int = 0,
    ):
        """Defines a set of weekly partitions.

        The first partition in the set will start at the start_date. The last partition in the set will
        end before the current time, unless the end_offset argument is set to a positive number. If
        day_offset is provided, the start and end date of each partition will be day of the week
        corresponding to day_offset (0 indexed with Sunday as the start of the week). If
        minute_offset and/or hour_offset are used, the start and end times of each partition will be
        hour_offset:minute_offset of each day.

        Args:
            start_date (Union[datetime.datetime, str]): The first date in the set of partitions will
                Sunday at midnight following start_date. Can provide in either a datetime or string
                format.
            minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
                to 0.
            hour_offset (int): Number of hours past 00:00 to "split" the partition. Defaults to 0.
            day_offset (int): Day of the week to "split" the partition. Defaults to 0 (Sunday).
            timezone (Optional[str]): The timezone in which each date should exist.
                Supported strings for timezones are the ones provided by the
                `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
            fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
            end_offset (int): Extends the partition set by a number of partitions equal to the value
                passed. If end_offset is 0 (the default), the last partition ends before the current
                time. If end_offset is 1, the second-to-last partition ends before the current time,
                and so on.

        .. code-block:: python

            WeeklyPartitionsDefinition(start_date="2022-03-12")
            # creates partitions (2022-03-13-00:00, 2022-03-20-00:00), (2022-03-20-00:00, 2022-03-27-00:00), ...

            WeeklyPartitionsDefinition(start_date="2022-03-12", minute_offset=15, hour_offset=3, day_offset=6)
            # creates partitions (2022-03-12-03:15, 2022-03-19-03:15), (2022-03-19-03:15, 2022-03-26-03:15), ...
        """
        _fmt = fmt or DEFAULT_DATE_FORMAT

        return super(WeeklyPartitionsDefinition, cls).__new__(
            cls,
            schedule_type=ScheduleType.WEEKLY,
            start=start_date,
            minute_offset=minute_offset,
            hour_offset=hour_offset,
            day_offset=day_offset,
            timezone=timezone,
            fmt=_fmt,
            end_offset=end_offset,
        )


def weekly_partitioned_config(
    start_date: Union[datetime, str],
    minute_offset: int = 0,
    hour_offset: int = 0,
    day_offset: int = 0,
    timezone: Optional[str] = None,
    fmt: Optional[str] = None,
    end_offset: int = 0,
    tags_for_partition_fn: Optional[Callable[[datetime, datetime], Mapping[str, str]]] = None,
) -> Callable[[Callable[[datetime, datetime], Mapping[str, Any]]], PartitionedConfig]:
    """Defines run config over a set of weekly partitions.

    The decorated function should accept a start datetime and end datetime, which represent the date
    partition the config should delineate.

    The decorated function should return a run config dictionary.

    The resulting object created by this decorator can be provided to the config argument of a Job.
    The first partition in the set will start at the start_date. The last partition in the set will
    end before the current time, unless the end_offset argument is set to a positive number. If
    day_offset is provided, the start and end date of each partition will be day of the week
    corresponding to day_offset (0 indexed with Sunday as the start of the week). If
    minute_offset and/or hour_offset are used, the start and end times of each partition will be
    hour_offset:minute_offset of each day.

    Args:
        start_date (Union[datetime.datetime, str]): The first date in the set of partitions will
            Sunday at midnight following start_date. Can provide in either a datetime or string
            format.
        minute_offset (int): Number of minutes past the hour to "split" the partition. Defaults
            to 0.
        hour_offset (int): Number of hours past 00:00 to "split" the partition. Defaults to 0.
        day_offset (int): Day of the week to "split" the partition. Defaults to 0 (Sunday).
        timezone (Optional[str]): The timezone in which each date should exist.
            Supported strings for timezones are the ones provided by the
            `IANA time zone database <https://www.iana.org/time-zones>` - e.g. "America/Los_Angeles".
        fmt (Optional[str]): The date format to use. Defaults to `%Y-%m-%d`.
        end_offset (int): Extends the partition set by a number of partitions equal to the value
            passed. If end_offset is 0 (the default), the last partition ends before the current
            time. If end_offset is 1, the second-to-last partition ends before the current time,
            and so on.

    .. code-block:: python

        @weekly_partitioned_config(start_date="2022-03-12")
        # creates partitions (2022-03-13-00:00, 2022-03-20-00:00), (2022-03-20-00:00, 2022-03-27-00:00), ...

        @weekly_partitioned_config(start_date="2022-03-12", minute_offset=15, hour_offset=3, day_offset=6)
        # creates partitions (2022-03-12-03:15, 2022-03-19-03:15), (2022-03-19-03:15, 2022-03-26-03:15), ...
    """

    def inner(fn: Callable[[datetime, datetime], Mapping[str, Any]]) -> PartitionedConfig:
        check.callable_param(fn, "fn")

        return PartitionedConfig(
            run_config_for_partition_fn=lambda partition: fn(
                partition.value[0], partition.value[1]
            ),
            partitions_def=WeeklyPartitionsDefinition(
                start_date=start_date,
                minute_offset=minute_offset,
                hour_offset=hour_offset,
                day_offset=day_offset,
                timezone=timezone,
                fmt=fmt,
                end_offset=end_offset,
            ),
            decorated_fn=fn,
            tags_for_partition_fn=wrap_time_window_tags_fn(tags_for_partition_fn),
        )

    return inner


class TimeWindowPartitionsSubset(PartitionsSubset):
    def __init__(
        self,
        partitions_def: TimeWindowPartitionsDefinition,
        key_ranges: Sequence[PartitionKeyRange],
    ):
        check.sequence_param(key_ranges, "key_ranges", of_type=PartitionKeyRange)
        self._partitions_def = partitions_def
        self._key_ranges = key_ranges

    def get_partition_keys_not_in_subset(
        self, current_time: Optional[datetime] = None
    ) -> Iterable[str]:
        cur_partition_key: Optional[str] = self._partitions_def.get_first_partition_key(
            current_time
        )
        included_ranges_iter = iter(self._key_ranges)
        cur_included_range = next(included_ranges_iter, None)

        result = []
        while cur_partition_key:
            if cur_included_range and cur_partition_key == cur_included_range.start:
                cur_partition_key = self._partitions_def.get_next_partition_key(
                    cur_included_range.end, current_time
                )
                cur_included_range = next(included_ranges_iter, None)
            else:
                result.append(cur_partition_key)
                cur_partition_key = self._partitions_def.get_next_partition_key(
                    cur_partition_key, current_time
                )

        return result

    def get_partition_keys(self) -> Iterable[str]:
        return set(self._partitions_def.get_partition_keys()) - set(
            self.get_partition_keys_not_in_subset()
        )

    @property
    def key_ranges(self) -> Sequence[PartitionKeyRange]:
        return self._key_ranges

    def with_partition_keys(self, partition_keys: Iterable[str]) -> "TimeWindowPartitionsSubset":
        result_key_ranges = [*self._key_ranges]
        for partition_key in partition_keys:
            # go in reverse order because it's more common to add partitions at the end than the
            # beginning
            for i in reversed(range(len(result_key_ranges))):
                key_range = result_key_ranges[i]
                lte_end_of_range = partition_key == key_range.end or self._partitions_def.less_than(
                    partition_key, key_range.end
                )
                gte_start_of_range = (
                    partition_key == key_range.start
                    or self._partitions_def.less_than(key_range.start, partition_key)
                )

                if lte_end_of_range and gte_start_of_range:
                    break

                if not lte_end_of_range:
                    merge_with_range = (
                        self._partitions_def.get_next_partition_key(key_range.end) == partition_key
                    )

                    merge_with_later_range = i + 1 < len(result_key_ranges) and (
                        self._partitions_def.get_next_partition_key(partition_key)
                        == result_key_ranges[i + 1].start
                    )

                    if merge_with_range and merge_with_later_range:
                        result_key_ranges[i] = PartitionKeyRange(
                            key_range.start, result_key_ranges[i + 1].end
                        )
                        del result_key_ranges[i + 1]
                    elif merge_with_range:
                        result_key_ranges[i] = PartitionKeyRange(key_range.start, partition_key)
                    else:
                        result_key_ranges.insert(
                            i + 1, PartitionKeyRange(partition_key, partition_key)
                        )

                    break
            else:
                if (
                    result_key_ranges
                    and self._partitions_def.get_next_partition_key(partition_key)
                    == result_key_ranges[0].start
                ):
                    result_key_ranges[0] = PartitionKeyRange(partition_key, key_range.end)
                else:
                    result_key_ranges.insert(0, PartitionKeyRange(partition_key, partition_key))

        return TimeWindowPartitionsSubset(self._partitions_def, result_key_ranges)

    @staticmethod
    def from_serialized(
        partitions_def: TimeWindowPartitionsDefinition, serialized: str
    ) -> "TimeWindowPartitionsSubset":
        key_ranges = [PartitionKeyRange(tup[0], tup[1]) for tup in json.loads(serialized)]
        return TimeWindowPartitionsSubset(partitions_def, key_ranges)

    def serialize(self) -> str:
        return json.dumps(self._key_ranges)
