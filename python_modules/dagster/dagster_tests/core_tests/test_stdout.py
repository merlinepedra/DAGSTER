import multiprocessing
import os
import random
import string
import sys
import tempfile
import time

import pytest

from dagster import (
    DagsterEventType,
    In,
    execute_job,
    fs_io_manager,
    job,
    op,
    reconstructable,
    resource,
)
from dagster._core.execution.compute_logs import should_disable_io_stream_redirect
from dagster._core.instance import DagsterInstance
from dagster._core.storage.compute_log_manager import ComputeIOType
from dagster._core.test_utils import create_run_for_test, instance_for_test
from dagster._utils import ensure_dir, touch_file

HELLO_SOLID = "HELLO SOLID"
HELLO_RESOURCE = "HELLO RESOURCE"
SEPARATOR = os.linesep if (os.name == "nt" and sys.version_info < (3,)) else "\n"


@resource
def resource_a(_):
    print(HELLO_RESOURCE)  # pylint: disable=print-call
    return "A"


@op
def spawn(_):
    return 1


@op(ins={"num": In(int)}, required_resource_keys={"a"})
def spew(_, num):
    print(HELLO_SOLID)  # pylint: disable=print-call
    return num


def define_pipeline():
    @job(resource_defs={"a": resource_a, "io_manager": fs_io_manager})
    def spew_job():
        spew(spew(spawn()))

    return spew_job


def normalize_file_content(s):
    return "\n".join([line for line in s.replace(os.linesep, "\n").split("\n") if line])


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_compute_log_to_disk():
    with instance_for_test() as instance:

        spew_job = define_pipeline()
        manager = instance.compute_log_manager
        result = spew_job.execute_in_process(instance=instance)
        assert result.success

        compute_steps = [
            event.step_key
            for event in result.all_events
            if event.event_type == DagsterEventType.STEP_START
        ]
        for step_key in compute_steps:
            if step_key.startswith("spawn"):
                continue
            compute_io_path = manager.get_local_path(result.run_id, step_key, ComputeIOType.STDOUT)
            assert os.path.exists(compute_io_path)
            with open(compute_io_path, "r", encoding="utf8") as stdout_file:
                assert normalize_file_content(stdout_file.read()) == HELLO_SOLID


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_compute_log_to_disk_multiprocess():
    with instance_for_test() as instance:
        manager = instance.compute_log_manager
        result = execute_job(reconstructable(define_pipeline), instance=instance)
        assert result.success

        compute_steps = [
            event.step_key
            for event in result.all_events
            if event.event_type == DagsterEventType.STEP_START
        ]
        for step_key in compute_steps:
            if step_key.startswith("spawn"):
                continue
            compute_io_path = manager.get_local_path(result.run_id, step_key, ComputeIOType.STDOUT)
            assert os.path.exists(compute_io_path)
            with open(compute_io_path, "r", encoding="utf8") as stdout_file:
                assert normalize_file_content(stdout_file.read()) == HELLO_SOLID


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_compute_log_manager():
    with instance_for_test() as instance:
        manager = instance.compute_log_manager
        spew_job = define_pipeline()
        result = spew_job.execute_in_process(instance=instance)
        assert result.success
        compute_steps = [
            event.step_key
            for event in result.all_events
            if event.event_type == DagsterEventType.STEP_START
        ]
        assert len(compute_steps) == 3
        step_key = "spew"
        assert manager.is_watch_completed(result.run_id, step_key)

        stdout = manager.read_logs_file(result.run_id, step_key, ComputeIOType.STDOUT)
        assert normalize_file_content(stdout.data) == HELLO_SOLID

        stderr = manager.read_logs_file(result.run_id, step_key, ComputeIOType.STDERR)
        cleaned_logs = stderr.data.replace("\x1b[34m", "").replace("\x1b[0m", "")
        assert "dagster - DEBUG - spew_job - " in cleaned_logs

        bad_logs = manager.read_logs_file("not_a_run_id", step_key, ComputeIOType.STDOUT)
        assert bad_logs.data is None
        assert not manager.is_watch_completed("not_a_run_id", step_key)


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_compute_log_manager_subscriptions():
    with instance_for_test() as instance:
        spew_job = define_pipeline()
        step_key = "spew"
        result = spew_job.execute_in_process(instance=instance)
        stdout_observable = instance.compute_log_manager.observable(
            result.run_id, step_key, ComputeIOType.STDOUT
        )
        stderr_observable = instance.compute_log_manager.observable(
            result.run_id, step_key, ComputeIOType.STDERR
        )
        stdout = []
        stdout_observable(stdout.append)
        stderr = []
        stderr_observable(stderr.append)
        assert len(stdout) == 1
        assert stdout[0].data.startswith(HELLO_SOLID)
        assert stdout[0].cursor in [12, 13]
        assert len(stderr) == 1
        assert stderr[0].cursor == len(stderr[0].data)
        assert stderr[0].cursor > 400


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_compute_log_manager_subscription_updates():
    from dagster._core.storage.local_compute_log_manager import LocalComputeLogManager

    with tempfile.TemporaryDirectory() as temp_dir:
        compute_log_manager = LocalComputeLogManager(temp_dir, polling_timeout=0.5)
        run_id = "fake_run_id"
        step_key = "spew"
        stdout_path = compute_log_manager.get_local_path(run_id, step_key, ComputeIOType.STDOUT)

        # make sure the parent directory to be watched exists, file exists
        ensure_dir(os.path.dirname(stdout_path))
        touch_file(stdout_path)

        # set up the subscription
        messages = []
        observable = compute_log_manager.observable(run_id, step_key, ComputeIOType.STDOUT)
        observable(messages.append)

        # returns a single update, with 0 data
        assert len(messages) == 1
        last_chunk = messages[-1]
        assert not last_chunk.data
        assert last_chunk.cursor == 0

        with open(stdout_path, "a+", encoding="utf8") as f:
            print(HELLO_SOLID, file=f)  # pylint:disable=print-call

        # wait longer than the watchdog timeout
        time.sleep(1)
        assert len(messages) == 2
        last_chunk = messages[-1]
        assert last_chunk.data
        assert last_chunk.cursor > 0


def gen_solid_name(length):
    return "".join(random.choice(string.ascii_lowercase) for x in range(length))


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_long_solid_names():
    solid_name = gen_solid_name(300)

    @job(resource_defs={"a": resource_a})
    def long_job():
        spew.alias(name=solid_name)()

    with instance_for_test() as instance:
        manager = instance.compute_log_manager

        result = long_job.execute_in_process(
            instance=instance,
            run_config={"solids": {solid_name: {"inputs": {"num": 1}}}},
        )
        assert result.success

        compute_steps = [
            event.step_key
            for event in result.all_events
            if event.event_type == DagsterEventType.STEP_START
        ]

        assert len(compute_steps) == 1
        step_key = compute_steps[0]
        assert manager.is_watch_completed(result.run_id, step_key)

        stdout = manager.read_logs_file(result.run_id, step_key, ComputeIOType.STDOUT)
        assert normalize_file_content(stdout.data) == HELLO_SOLID


def execute_inner(step_key, pipeline_run, instance_ref):
    instance = DagsterInstance.from_ref(instance_ref)
    inner_step(instance, pipeline_run, step_key)


def inner_step(instance, pipeline_run, step_key):
    with instance.compute_log_manager.watch(pipeline_run, step_key=step_key):
        time.sleep(0.1)
        print(step_key, "inner 1")  # pylint: disable=print-call
        print(step_key, "inner 2")  # pylint: disable=print-call
        print(step_key, "inner 3")  # pylint: disable=print-call
        time.sleep(0.1)


def expected_inner_output(step_key):
    return "\n".join(
        ["{step_key} inner {num}".format(step_key=step_key, num=i + 1) for i in range(3)]
    )


def expected_outer_prefix():
    return "\n".join(["outer {num}".format(num=i + 1) for i in range(3)])


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_single():
    with instance_for_test() as instance:
        pipeline_name = "foo_pipeline"
        pipeline_run = create_run_for_test(instance, pipeline_name=pipeline_name)

        step_keys = ["A", "B", "C"]

        with instance.compute_log_manager.watch(pipeline_run):
            print("outer 1")  # pylint: disable=print-call
            print("outer 2")  # pylint: disable=print-call
            print("outer 3")  # pylint: disable=print-call

            for step_key in step_keys:
                inner_step(instance, pipeline_run, step_key)

        for step_key in step_keys:
            stdout = instance.compute_log_manager.read_logs_file(
                pipeline_run.run_id, step_key, ComputeIOType.STDOUT
            )
            assert normalize_file_content(stdout.data) == expected_inner_output(step_key)

        full_out = instance.compute_log_manager.read_logs_file(
            pipeline_run.run_id, pipeline_name, ComputeIOType.STDOUT
        )

        assert normalize_file_content(full_out.data).startswith(expected_outer_prefix())


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_compute_log_base_with_spaces():
    with tempfile.TemporaryDirectory() as temp_dir:
        with instance_for_test(
            temp_dir=temp_dir,
            overrides={
                "compute_logs": {
                    "module": "dagster._core.storage.local_compute_log_manager",
                    "class": "LocalComputeLogManager",
                    "config": {"base_dir": os.path.join(temp_dir, "base with spaces")},
                }
            },
        ) as instance:
            pipeline_name = "foo_pipeline"
            pipeline_run = create_run_for_test(instance, pipeline_name=pipeline_name)

            step_keys = ["A", "B", "C"]

            with instance.compute_log_manager.watch(pipeline_run):
                print("outer 1")  # pylint: disable=print-call
                print("outer 2")  # pylint: disable=print-call
                print("outer 3")  # pylint: disable=print-call

                for step_key in step_keys:
                    inner_step(instance, pipeline_run, step_key)

            for step_key in step_keys:
                stdout = instance.compute_log_manager.read_logs_file(
                    pipeline_run.run_id, step_key, ComputeIOType.STDOUT
                )
                assert normalize_file_content(stdout.data) == expected_inner_output(step_key)

            full_out = instance.compute_log_manager.read_logs_file(
                pipeline_run.run_id, pipeline_name, ComputeIOType.STDOUT
            )

            assert normalize_file_content(full_out.data).startswith(expected_outer_prefix())


@pytest.mark.skipif(
    should_disable_io_stream_redirect(), reason="compute logs disabled for win / py3.6+"
)
def test_multi():
    ctx = multiprocessing.get_context("spawn")

    with instance_for_test() as instance:
        pipeline_name = "foo_pipeline"
        pipeline_run = create_run_for_test(instance, pipeline_name=pipeline_name)

        step_keys = ["A", "B", "C"]

        with instance.compute_log_manager.watch(pipeline_run):
            print("outer 1")  # pylint: disable=print-call
            print("outer 2")  # pylint: disable=print-call
            print("outer 3")  # pylint: disable=print-call

            for step_key in step_keys:
                process = ctx.Process(
                    target=execute_inner,
                    args=(step_key, pipeline_run, instance.get_ref()),
                )
                process.start()
                process.join()

        for step_key in step_keys:
            stdout = instance.compute_log_manager.read_logs_file(
                pipeline_run.run_id, step_key, ComputeIOType.STDOUT
            )
            assert normalize_file_content(stdout.data) == expected_inner_output(step_key)

        full_out = instance.compute_log_manager.read_logs_file(
            pipeline_run.run_id, pipeline_name, ComputeIOType.STDOUT
        )

        # The way that the multiprocess compute-logging interacts with pytest (which stubs out the
        # sys.stdout fileno) makes this difficult to test.  The pytest-captured stdout only captures
        # the stdout from the outer process, not also the inner process
        assert normalize_file_content(full_out.data).startswith(expected_outer_prefix())
