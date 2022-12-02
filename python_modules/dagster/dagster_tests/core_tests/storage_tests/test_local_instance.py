import os
import tempfile
import time
import types

import pytest
import yaml

from dagster import DagsterEventType, DagsterInvalidConfigError, In, Out, Output
from dagster import _check as check
from dagster import job, op
from dagster._core.definitions.events import RetryRequested
from dagster._core.execution.stats import StepEventStatus
from dagster._core.instance import DagsterInstance, InstanceRef, InstanceType
from dagster._core.launcher import DefaultRunLauncher
from dagster._core.run_coordinator import DefaultRunCoordinator
from dagster._core.storage.event_log import SqliteEventLogStorage
from dagster._core.storage.local_compute_log_manager import LocalComputeLogManager
from dagster._core.storage.pipeline_run import DagsterRunStatus
from dagster._core.storage.root import LocalArtifactStorage
from dagster._core.storage.runs import SqliteRunStorage
from dagster._core.test_utils import environ
from dagster._legacy import DagsterRun


def test_fs_stores():
    @job
    def simple():
        @op
        def easy(context):
            context.log.info("easy")
            return "easy"

        easy()

    with tempfile.TemporaryDirectory() as temp_dir:
        with environ({"DAGSTER_HOME": temp_dir}):
            run_store = SqliteRunStorage.from_local(temp_dir)
            event_store = SqliteEventLogStorage(temp_dir)
            compute_log_manager = LocalComputeLogManager(temp_dir)
            instance = DagsterInstance(
                instance_type=InstanceType.PERSISTENT,
                local_artifact_storage=LocalArtifactStorage(temp_dir),
                run_storage=run_store,
                event_storage=event_store,
                compute_log_manager=compute_log_manager,
                run_coordinator=DefaultRunCoordinator(),
                run_launcher=DefaultRunLauncher(),
                ref=InstanceRef.from_dir(temp_dir),
                settings={"telemetry": {"enabled": False}},
            )

            result = simple.execute_in_process(instance=instance)

            assert run_store.has_run(result.run_id)
            assert run_store.get_run_by_id(result.run_id).status == DagsterRunStatus.SUCCESS
            assert DagsterEventType.PIPELINE_SUCCESS in [
                event.dagster_event.event_type
                for event in event_store.get_logs_for_run(result.run_id)
                if event.is_dagster_event
            ]
            stats = event_store.get_stats_for_run(result.run_id)
            assert stats.steps_succeeded == 1
            assert stats.end_time is not None


def test_init_compute_log_with_bad_config():
    with tempfile.TemporaryDirectory() as tmpdir_path:
        with open(os.path.join(tmpdir_path, "dagster.yaml"), "w", encoding="utf8") as fd:
            yaml.dump({"compute_logs": {"garbage": "flargh"}}, fd, default_flow_style=False)
        with pytest.raises(
            DagsterInvalidConfigError,
            match='Received unexpected config entry "garbage"',
        ):
            DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))


def test_init_compute_log_with_bad_config_override():
    with tempfile.TemporaryDirectory() as tmpdir_path:
        with pytest.raises(
            DagsterInvalidConfigError,
            match='Received unexpected config entry "garbage"',
        ):
            DagsterInstance.from_ref(
                InstanceRef.from_dir(tmpdir_path, overrides={"compute_logs": {"garbage": "flargh"}})
            )


def test_init_compute_log_with_bad_config_module():
    with tempfile.TemporaryDirectory() as tmpdir_path:
        with open(os.path.join(tmpdir_path, "dagster.yaml"), "w", encoding="utf8") as fd:
            yaml.dump(
                {"compute_logs": {"module": "flargh", "class": "Woble", "config": {}}},
                fd,
                default_flow_style=False,
            )
        with pytest.raises(check.CheckError, match="Couldn't import module"):
            DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))


MOCK_HAS_RUN_CALLED = False


def test_get_run_by_id():
    with tempfile.TemporaryDirectory() as tmpdir_path:
        instance = DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))

        assert instance.get_runs() == []
        pipeline_run = DagsterRun("foo_pipeline", "new_run")
        assert instance.get_run_by_id(pipeline_run.run_id) is None

        instance._run_storage.add_run(pipeline_run)  # pylint: disable=protected-access

        assert instance.get_runs() == [pipeline_run]

        assert instance.get_run_by_id(pipeline_run.run_id) == pipeline_run

    # Run is created after we check whether it exists
    with tempfile.TemporaryDirectory() as tmpdir_path:
        instance = DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))
        run = DagsterRun(pipeline_name="foo_pipeline", run_id="bar_run")

        def _has_run(self, run_id):
            # This is uglier than we would like because there is no nonlocal keyword in py2
            global MOCK_HAS_RUN_CALLED  # pylint: disable=global-variable-not-assigned
            # pylint: disable=protected-access
            if not self._run_storage.has_run(run_id) and not MOCK_HAS_RUN_CALLED:
                self._run_storage.add_run(DagsterRun(pipeline_name="foo_pipeline", run_id=run_id))
                return False
            else:
                return self._run_storage.has_run(run_id)

        instance.has_run = types.MethodType(_has_run, instance)

        assert instance.get_run_by_id(run.run_id) is None

    # Run is created after we check whether it exists, but deleted before we can get it
    global MOCK_HAS_RUN_CALLED  # pylint:disable=global-statement
    MOCK_HAS_RUN_CALLED = False
    with tempfile.TemporaryDirectory() as tmpdir_path:
        instance = DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))
        run = DagsterRun(pipeline_name="foo_pipeline", run_id="bar_run")

        def _has_run(self, run_id):
            global MOCK_HAS_RUN_CALLED  # pylint: disable=global-statement
            # pylint: disable=protected-access
            if not self._run_storage.has_run(run_id) and not MOCK_HAS_RUN_CALLED:
                self._run_storage.add_run(DagsterRun(pipeline_name="foo_pipeline", run_id=run_id))
                MOCK_HAS_RUN_CALLED = True
                return False
            elif self._run_storage.has_run(run_id) and MOCK_HAS_RUN_CALLED:
                MOCK_HAS_RUN_CALLED = False
                return True
            else:
                return False

        instance.has_run = types.MethodType(_has_run, instance)
        assert instance.get_run_by_id(run.run_id) is None


def test_run_step_stats():
    _called = None

    @job
    def simple():
        @op
        def should_succeed(context):
            time.sleep(0.001)
            context.log.info("succeed")
            return "yay"

        @op(
            ins={"_input": In(str)},
            out=Out(str),
        )
        def should_fail(context, _input):
            context.log.info("fail")
            raise Exception("booo")

        @op
        def should_not_execute(_, x):
            _called = True
            return x

        should_not_execute(should_fail(should_succeed()))

    with tempfile.TemporaryDirectory() as tmpdir_path:
        instance = DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))
        result = simple.execute_in_process(instance=instance, raise_on_error=False)
        step_stats = sorted(instance.get_run_step_stats(result.run_id), key=lambda x: x.end_time)
        assert len(step_stats) == 2
        assert step_stats[0].step_key == "should_succeed"
        assert step_stats[0].status == StepEventStatus.SUCCESS
        assert step_stats[0].end_time > step_stats[0].start_time
        assert step_stats[0].attempts == 1
        assert step_stats[1].step_key == "should_fail"
        assert step_stats[1].status == StepEventStatus.FAILURE
        assert step_stats[1].end_time > step_stats[0].start_time
        assert step_stats[1].attempts == 1
        assert not _called


def test_run_step_stats_with_retries():
    _called = None
    _count = {"total": 0}

    @job
    def simple():
        @op
        def should_succeed(_):
            # This is to have at least one other step that retried to properly test
            # the step key filter on `get_run_step_stats`
            if _count["total"] < 2:
                _count["total"] += 1
                raise RetryRequested(max_retries=3)

            yield Output("yay")

        @op(
            ins={"_input": In(str)},
            out=Out(str),
        )
        def should_retry(context, _input):
            raise RetryRequested(max_retries=3)

        @op
        def should_not_execute(_, x):
            _called = True
            return x

        should_not_execute(should_retry(should_succeed()))

    with tempfile.TemporaryDirectory() as tmpdir_path:
        instance = DagsterInstance.from_ref(InstanceRef.from_dir(tmpdir_path))
        result = simple.execute_in_process(instance=instance, raise_on_error=False)
        step_stats = instance.get_run_step_stats(result.run_id, step_keys=["should_retry"])
        assert len(step_stats) == 1
        assert step_stats[0].step_key == "should_retry"
        assert step_stats[0].status == StepEventStatus.FAILURE
        assert step_stats[0].end_time > step_stats[0].start_time
        assert step_stats[0].attempts == 4
        assert not _called
