import string

from dagster import op, ScheduleDefinition, repository, job
from dagster._legacy import PartitionSetDefinition, pipeline


@op
def do_something():
    return 1


@op
def do_input(x):
    return x


@job(name="foo")
def foo_job():
    do_input(do_something())


def define_foo_job():
    return foo_job


@job(name="baz", description="Not much tbh")
def baz_job():
    do_input()


def define_bar_schedules():
    return {
        "foo_schedule": ScheduleDefinition(
            "foo_schedule",
            cron_schedule="* * * * *",
            job_name="foo",
            run_config={},
        )
    }


def define_baz_partitions():
    return {
        "baz_partitions": PartitionSetDefinition(
            name="baz_partitions",
            pipeline_name="baz",
            partition_fn=lambda: string.ascii_lowercase,
            run_config_fn_for_partition=lambda partition: {
                "solids": {"do_input": {"inputs": {"x": {"value": partition.value}}}}
            },
        )
    }


@repository
def bar():
    return {
        "pipelines": {"foo": foo_job, "baz": baz_job},
        "schedules": define_bar_schedules(),
        "partition_sets": define_baz_partitions(),
    }
