import string

from dagster import (
    job,
    In,
    Out,
    Int,
    ScheduleDefinition,
    op,
    repository,
    usable_as_dagster_type,
)
from dagster._core.definitions.decorators.sensor_decorator import sensor
from dagster._core.definitions.sensor_definition import RunRequest
from dagster._core.errors import DagsterError
from dagster._core.test_utils import default_mode_def_for_test
from dagster._legacy import (
    PartitionSetDefinition,
    pipeline,
)


@op
def do_something():
    return 1


@op
def do_input(x):
    return x


@pipeline(name="foo", mode_defs=[default_mode_def_for_test])
def foo_job():
    do_input(do_something())


@op
def do_fail():
    raise Exception("I have failed")


@job
def fail_job():
    do_fail()


@pipeline(name="baz", description="Not much tbh")
def baz_job():
    do_input()


def define_foo_pipeline():
    return foo_job


@pipeline(name="other_foo")
def other_foo_job():
    do_input(do_something())


def define_other_foo_pipeline():
    return other_foo_job


@pipeline(name="bar")
def bar_job():
    @usable_as_dagster_type(name="InputTypeWithoutHydration")
    class InputTypeWithoutHydration(int):
        pass

    @op(out=Out(InputTypeWithoutHydration))
    def one(_):
        return 1

    @op(
        ins={"some_input": In(InputTypeWithoutHydration)},
        out=Out(Int),
    )
    def fail_subset(_, some_input):
        return some_input

    return fail_subset(one())


def define_bar_schedules():
    return {
        "foo_schedule": ScheduleDefinition(
            "foo_schedule",
            cron_schedule="* * * * *",
            job_name="foo",
            run_config={"fizz": "buzz"},
        ),
        "foo_schedule_never_execute": ScheduleDefinition(
            "foo_schedule_never_execute",
            cron_schedule="* * * * *",
            job_name="foo",
            run_config={"fizz": "buzz"},
            should_execute=lambda _context: False,
        ),
        "foo_schedule_echo_time": ScheduleDefinition(
            "foo_schedule_echo_time",
            cron_schedule="* * * * *",
            job_name="foo",
            run_config_fn=lambda context: {
                "passed_in_time": context.scheduled_execution_time.isoformat()
                if context.scheduled_execution_time
                else ""
            },
        ),
    }


def error_partition_fn():
    raise Exception("womp womp")


def error_partition_config_fn():
    raise Exception("womp womp")


def error_partition_tags_fn(_partition):
    raise Exception("womp womp")


def define_baz_partitions():
    return {
        "baz_partitions": PartitionSetDefinition(
            name="baz_partitions",
            pipeline_name="baz",
            partition_fn=lambda: string.ascii_lowercase,
            run_config_fn_for_partition=lambda partition: {
                "solids": {"do_input": {"inputs": {"x": {"value": partition.value}}}}
            },
            tags_fn_for_partition=lambda _partition: {"foo": "bar"},
        ),
        "error_partitions": PartitionSetDefinition(
            name="error_partitions",
            pipeline_name="baz",
            partition_fn=error_partition_fn,
            run_config_fn_for_partition=lambda partition: {},
        ),
        "error_partition_config": PartitionSetDefinition(
            name="error_partition_config",
            pipeline_name="baz",
            partition_fn=lambda: string.ascii_lowercase,
            run_config_fn_for_partition=error_partition_config_fn,
        ),
        "error_partition_tags": PartitionSetDefinition(
            name="error_partition_tags",
            pipeline_name="baz",
            partition_fn=lambda: string.ascii_lowercase,
            run_config_fn_for_partition=lambda partition: {},
            tags_fn_for_partition=error_partition_tags_fn,
        ),
    }


@sensor(job_name="foo")
def sensor_foo(_):
    yield RunRequest(run_key=None, run_config={"foo": "FOO"}, tags={"foo": "foo_tag"})
    yield RunRequest(run_key=None, run_config={"foo": "FOO"})


@sensor(job_name="foo")
def sensor_error(_):
    raise Exception("womp womp")


@sensor(job_name="foo")
def sensor_raises_dagster_error(_):
    raise DagsterError("Dagster error")


@repository
def bar_repo():
    return {
        "pipelines": {
            "foo": define_foo_pipeline,
            "bar": lambda: bar_job,
            "baz": lambda: baz_job,
            "fail": fail_job,
        },
        "schedules": define_bar_schedules(),
        "partition_sets": define_baz_partitions(),
        "sensors": {
            "sensor_foo": sensor_foo,
            "sensor_error": lambda: sensor_error,
            "sensor_raises_dagster_error": lambda: sensor_raises_dagster_error,
        },
    }


@repository
def other_repo():
    return {"pipelines": {"other_foo": define_other_foo_pipeline}}
