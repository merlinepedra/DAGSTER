# pylint: disable=redefined-outer-name

from contextlib import contextmanager

import pytest
from dagster_tests.api_tests.utils import get_foo_job_handle

from dagster._core.host_representation.repository_location import GrpcServerRepositoryLocation
from dagster._core.storage.pipeline_run import IN_PROGRESS_RUN_STATUSES, DagsterRunStatus
from dagster._core.storage.tags import PRIORITY_TAG
from dagster._core.test_utils import (
    create_run_for_test,
    create_test_daemon_workspace_context,
    instance_for_test,
)
from dagster._core.workspace.load_target import EmptyWorkspaceTarget
from dagster._daemon.run_coordinator.queued_run_coordinator_daemon import QueuedRunCoordinatorDaemon


@contextmanager
def instance_for_queued_run_coordinator(max_concurrent_runs=None, tag_concurrency_limits=None):
    max_concurrent_runs = (
        {"max_concurrent_runs": max_concurrent_runs} if max_concurrent_runs else {}
    )
    tag_concurrency_limits = (
        {"tag_concurrency_limits": tag_concurrency_limits} if tag_concurrency_limits else {}
    )
    overrides = {
        "run_coordinator": {
            "module": "dagster._core.run_coordinator",
            "class": "QueuedRunCoordinator",
            "config": {**max_concurrent_runs, **tag_concurrency_limits},
        },
        "run_launcher": {
            "module": "dagster._core.test_utils",
            "class": "MockedRunLauncher",
            "config": {"bad_run_ids": ["bad-run"]},
        },
    }

    with instance_for_test(overrides=overrides) as instance:
        yield instance


@pytest.fixture(name="instance")
def instance_fixture():
    with instance_for_queued_run_coordinator(max_concurrent_runs=10) as instance:
        yield instance


@pytest.fixture(name="daemon")
def daemon_fixture():
    return QueuedRunCoordinatorDaemon(interval_seconds=1)


@pytest.fixture(name="workspace_context")
def workspace_fixture(instance):
    with create_test_daemon_workspace_context(
        workspace_load_target=EmptyWorkspaceTarget(), instance=instance
    ) as workspace_context:
        yield workspace_context


@pytest.fixture(scope="module")
def pipeline_handle():
    with get_foo_job_handle() as handle:
        yield handle


def create_run(instance, pipeline_handle, **kwargs):
    create_run_for_test(
        instance,
        external_pipeline_origin=pipeline_handle.get_external_origin(),
        pipeline_code_origin=pipeline_handle.get_python_origin(),
        pipeline_name="foo",
        **kwargs,
    )


def get_run_ids(runs_queue):
    return [run.run_id for run in runs_queue]


def test_attempt_to_launch_runs_filter(instance, workspace_context, daemon, pipeline_handle):
    create_run(
        instance,
        pipeline_handle,
        run_id="queued-run",
        status=DagsterRunStatus.QUEUED,
    )
    create_run(
        instance,
        pipeline_handle,
        run_id="non-queued-run",
        status=DagsterRunStatus.NOT_STARTED,
    )

    list(daemon.run_iteration(workspace_context))

    assert get_run_ids(instance.run_launcher.queue()) == ["queued-run"]


def test_attempt_to_launch_runs_no_queued(instance, pipeline_handle, workspace_context, daemon):
    create_run(
        instance,
        pipeline_handle,
        run_id="queued-run",
        status=DagsterRunStatus.STARTED,
    )
    create_run(
        instance,
        pipeline_handle,
        run_id="non-queued-run",
        status=DagsterRunStatus.NOT_STARTED,
    )

    list(daemon.run_iteration(workspace_context))

    assert instance.run_launcher.queue() == []


@pytest.mark.parametrize(
    "num_in_progress_runs",
    [0, 1, 5],
)
def test_get_queued_runs_max_runs(num_in_progress_runs, pipeline_handle, workspace_context, daemon):
    max_runs = 4
    with instance_for_queued_run_coordinator(max_concurrent_runs=max_runs) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)
        # fill run store with ongoing runs
        in_progress_run_ids = ["in_progress-run-{}".format(i) for i in range(num_in_progress_runs)]
        for i, run_id in enumerate(in_progress_run_ids):
            # get a selection of all in progress statuses
            status = IN_PROGRESS_RUN_STATUSES[i % len(IN_PROGRESS_RUN_STATUSES)]
            create_run(
                instance,
                pipeline_handle,
                run_id=run_id,
                status=status,
            )

        # add more queued runs than should be launched
        queued_run_ids = ["queued-run-{}".format(i) for i in range(max_runs + 1)]
        for run_id in queued_run_ids:
            create_run(
                instance,
                pipeline_handle,
                run_id=run_id,
                status=DagsterRunStatus.QUEUED,
            )

        list(daemon.run_iteration(bounded_ctx))

        assert len(instance.run_launcher.queue()) == max(0, max_runs - num_in_progress_runs)


def test_disable_max_concurrent_runs_limit(workspace_context, pipeline_handle, daemon):
    with instance_for_queued_run_coordinator(max_concurrent_runs=-1) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        # create ongoing runs
        in_progress_run_ids = ["in_progress-run-{}".format(i) for i in range(5)]
        for i, run_id in enumerate(in_progress_run_ids):
            # get a selection of all in progress statuses
            status = IN_PROGRESS_RUN_STATUSES[i % len(IN_PROGRESS_RUN_STATUSES)]
            create_run(
                instance,
                pipeline_handle,
                run_id=run_id,
                status=status,
            )

        # add more queued runs
        queued_run_ids = ["queued-run-{}".format(i) for i in range(6)]
        for run_id in queued_run_ids:
            create_run(
                instance,
                pipeline_handle,
                run_id=run_id,
                status=DagsterRunStatus.QUEUED,
            )

        list(daemon.run_iteration(bounded_ctx))

        assert len(instance.run_launcher.queue()) == 6


def test_priority(instance, workspace_context, pipeline_handle, daemon):
    create_run(instance, pipeline_handle, run_id="default-pri-run", status=DagsterRunStatus.QUEUED)
    create_run(
        instance,
        pipeline_handle,
        run_id="low-pri-run",
        status=DagsterRunStatus.QUEUED,
        tags={PRIORITY_TAG: "-1"},
    )
    create_run(
        instance,
        pipeline_handle,
        run_id="hi-pri-run",
        status=DagsterRunStatus.QUEUED,
        tags={PRIORITY_TAG: "3"},
    )

    list(daemon.run_iteration(workspace_context))

    assert get_run_ids(instance.run_launcher.queue()) == [
        "hi-pri-run",
        "default-pri-run",
        "low-pri-run",
    ]


def test_priority_on_malformed_tag(instance, workspace_context, pipeline_handle, daemon):
    create_run(
        instance,
        pipeline_handle,
        run_id="bad-pri-run",
        status=DagsterRunStatus.QUEUED,
        tags={PRIORITY_TAG: "foobar"},
    )

    list(daemon.run_iteration(workspace_context))

    assert get_run_ids(instance.run_launcher.queue()) == ["bad-pri-run"]


def test_tag_limits(workspace_context, pipeline_handle, daemon):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[{"key": "database", "value": "tiny", "limit": 1}],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="tiny-1",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "tiny"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="tiny-2",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "tiny"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="large-1",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "large"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["tiny-1", "large-1"]


def test_tag_limits_just_key(workspace_context, pipeline_handle, daemon):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[
            {"key": "database", "value": {"applyLimitPerUniqueValue": False}, "limit": 2}
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="tiny-1",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "tiny"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="tiny-2",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "tiny"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="large-1",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "large"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["tiny-1", "tiny-2"]


def test_multiple_tag_limits(
    workspace_context,
    daemon,
    pipeline_handle,
):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[
            {"key": "database", "value": "tiny", "limit": 1},
            {"key": "user", "value": "johann", "limit": 2},
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="run-1",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "tiny", "user": "johann"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-2",
            status=DagsterRunStatus.QUEUED,
            tags={"database": "tiny"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-3",
            status=DagsterRunStatus.QUEUED,
            tags={"user": "johann"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-4",
            status=DagsterRunStatus.QUEUED,
            tags={"user": "johann"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["run-1", "run-3"]


def test_overlapping_tag_limits(workspace_context, daemon, pipeline_handle):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[
            {"key": "foo", "limit": 2},
            {"key": "foo", "value": "bar", "limit": 1},
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="run-1",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-2",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-3",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "other"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-4",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "other"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["run-1", "run-3"]


def test_limits_per_unique_value(workspace_context, pipeline_handle, daemon):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[
            {"key": "foo", "limit": 1, "value": {"applyLimitPerUniqueValue": True}},
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="run-1",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-2",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        list(daemon.run_iteration(bounded_ctx))

        create_run(
            instance,
            pipeline_handle,
            run_id="run-3",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "other"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-4",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "other"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["run-1", "run-3"]


def test_limits_per_unique_value_overlapping_limits(workspace_context, daemon, pipeline_handle):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[
            {"key": "foo", "limit": 1, "value": {"applyLimitPerUniqueValue": True}},
            {"key": "foo", "limit": 2},
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="run-1",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-2",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-3",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "other"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-4",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "other-2"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["run-1", "run-3"]

    with instance_for_queued_run_coordinator(
        max_concurrent_runs=10,
        tag_concurrency_limits=[
            {"key": "foo", "limit": 2, "value": {"applyLimitPerUniqueValue": True}},
            {"key": "foo", "limit": 1, "value": "bar"},
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="run-1",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-2",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "baz"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-3",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "bar"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-4",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "baz"},
        )
        create_run(
            instance,
            pipeline_handle,
            run_id="run-5",
            status=DagsterRunStatus.QUEUED,
            tags={"foo": "baz"},
        )

        list(daemon.run_iteration(bounded_ctx))

        assert get_run_ids(instance.run_launcher.queue()) == ["run-1", "run-2", "run-4"]


def test_locations_not_created(instance, monkeypatch, workspace_context, daemon, pipeline_handle):
    """
    Verifies that no repository location is created when runs are dequeued
    """

    create_run(
        instance,
        pipeline_handle,
        run_id="queued-run",
        status=DagsterRunStatus.QUEUED,
    )

    create_run(
        instance,
        pipeline_handle,
        run_id="queued-run-2",
        status=DagsterRunStatus.QUEUED,
    )

    original_method = GrpcServerRepositoryLocation.__init__

    method_calls = []

    def mocked_location_init(
        self,
        origin,
        host=None,
        port=None,
        socket=None,
        server_id=None,
        heartbeat=False,
        watch_server=True,
        grpc_server_registry=None,
    ):
        method_calls.append(origin)
        return original_method(
            self,
            origin,
            host,
            port,
            socket,
            server_id,
            heartbeat,
            watch_server,
            grpc_server_registry,
        )

    monkeypatch.setattr(
        GrpcServerRepositoryLocation,
        "__init__",
        mocked_location_init,
    )

    list(daemon.run_iteration(workspace_context))

    assert get_run_ids(instance.run_launcher.queue()) == ["queued-run", "queued-run-2"]
    assert len(method_calls) == 0


def test_skip_error_runs(instance, pipeline_handle, workspace_context, daemon):
    create_run(
        instance,
        pipeline_handle,
        run_id="bad-run",
        status=DagsterRunStatus.QUEUED,
    )

    create_run(
        instance,
        pipeline_handle,
        run_id="good-run",
        status=DagsterRunStatus.QUEUED,
    )

    errors = [error for error in list(daemon.run_iteration(workspace_context)) if error]

    assert len(errors) == 1
    assert "Bad run bad-run" in errors[0].message

    assert get_run_ids(instance.run_launcher.queue()) == ["good-run"]
    assert instance.get_run_by_id("bad-run").status == DagsterRunStatus.FAILURE


def test_key_limit_with_extra_tags(workspace_context, daemon, pipeline_handle):
    with instance_for_queued_run_coordinator(
        max_concurrent_runs=2,
        tag_concurrency_limits=[
            {"key": "test", "limit": 1},
        ],
    ) as instance:
        bounded_ctx = workspace_context.copy_for_test_instance(instance)

        create_run(
            instance,
            pipeline_handle,
            run_id="run-1",
            status=DagsterRunStatus.QUEUED,
            tags={"other-tag": "value", "test": "value"},
        )

        create_run(
            instance,
            pipeline_handle,
            run_id="run-2",
            status=DagsterRunStatus.QUEUED,
            tags={"other-tag": "value", "test": "value"},
        )

        list(daemon.run_iteration(bounded_ctx))
        assert get_run_ids(instance.run_launcher.queue()) == ["run-1"]
