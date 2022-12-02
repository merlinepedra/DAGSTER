# pylint: disable=redefined-outer-name

import logging
import os
import time

import pytest

from dagster._core.events import DagsterEvent, DagsterEventType
from dagster._core.events.log import EventLogEntry
from dagster._core.launcher import CheckRunHealthResult, RunLauncher, WorkerStatus
from dagster._core.storage.pipeline_run import DagsterRunStatus
from dagster._core.test_utils import (
    create_run_for_test,
    create_test_daemon_workspace_context,
    environ,
    instance_for_test,
)
from dagster._core.workspace.load_target import EmptyWorkspaceTarget
from dagster._daemon import get_default_daemon_logger
from dagster._daemon.monitoring.monitoring_daemon import monitor_started_run, monitor_starting_run
from dagster._serdes import ConfigurableClass


class TestRunLauncher(RunLauncher, ConfigurableClass):
    def __init__(self, inst_data=None):
        self._inst_data = inst_data
        self.launch_run_calls = 0
        self.resume_run_calls = 0
        super().__init__()

    @property
    def inst_data(self):
        return self._inst_data

    @classmethod
    def config_type(cls):
        return {}

    @staticmethod
    def from_config_value(inst_data, config_value):
        return TestRunLauncher(inst_data=inst_data)

    def launch_run(self, context):
        self.launch_run_calls += 1

    def resume_run(self, context):
        self.resume_run_calls += 1

    def join(self, timeout=30):
        pass

    def terminate(self, run_id):
        raise NotImplementedError()

    @property
    def supports_resume_run(self):
        return True

    @property
    def supports_check_run_worker_health(self):
        return True

    def check_run_worker_health(self, _run):
        return (
            CheckRunHealthResult(WorkerStatus.RUNNING, "")
            if os.environ.get("DAGSTER_TEST_RUN_HEALTH_CHECK_RESULT") == "healthy"
            else CheckRunHealthResult(WorkerStatus.NOT_FOUND, "")
        )


@pytest.fixture
def instance():
    with instance_for_test(
        overrides={
            "run_launcher": {
                "module": "dagster_tests.daemon_tests.test_monitoring_daemon",
                "class": "TestRunLauncher",
            },
            "run_monitoring": {"enabled": True},
        },
    ) as instance:
        yield instance


@pytest.fixture
def workspace(instance):
    with create_test_daemon_workspace_context(
        workspace_load_target=EmptyWorkspaceTarget(), instance=instance
    ) as workspace:
        yield workspace


@pytest.fixture
def logger():
    return get_default_daemon_logger("MonitoringDaemon")


def report_starting_event(instance, run, timestamp):
    launch_started_event = DagsterEvent(
        event_type_value=DagsterEventType.PIPELINE_STARTING.value,
        pipeline_name=run.pipeline_name,
    )

    event_record = EventLogEntry(
        user_message="",
        level=logging.INFO,
        pipeline_name=run.pipeline_name,
        run_id=run.run_id,
        error_info=None,
        timestamp=timestamp,
        dagster_event=launch_started_event,
    )

    instance.handle_new_event(event_record)


def test_monitor_starting(instance, logger):
    run = create_run_for_test(
        instance,
        pipeline_name="foo",
    )
    report_starting_event(instance, run, timestamp=time.time())
    monitor_starting_run(
        instance,
        instance.get_run_by_id(run.run_id),
        logger,
    )
    assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.STARTING

    run = create_run_for_test(instance, pipeline_name="foo")
    report_starting_event(instance, run, timestamp=time.time() - 1000)

    monitor_starting_run(
        instance,
        instance.get_run_by_id(run.run_id),
        logger,
    )
    assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.FAILURE


def test_monitor_started(instance, workspace, logger):

    run = create_run_for_test(instance, pipeline_name="foo", status=DagsterRunStatus.STARTED)
    with environ({"DAGSTER_TEST_RUN_HEALTH_CHECK_RESULT": "healthy"}):
        monitor_started_run(instance, workspace, run, logger)
        assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.STARTED
        assert instance.run_launcher.launch_run_calls == 0
        assert instance.run_launcher.resume_run_calls == 0

    monitor_started_run(instance, workspace, run, logger)
    assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.STARTED
    assert instance.run_launcher.launch_run_calls == 0
    assert instance.run_launcher.resume_run_calls == 1

    monitor_started_run(instance, workspace, run, logger)
    assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.STARTED
    assert instance.run_launcher.launch_run_calls == 0
    assert instance.run_launcher.resume_run_calls == 2

    monitor_started_run(instance, workspace, run, logger)
    assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.STARTED
    assert instance.run_launcher.launch_run_calls == 0
    assert instance.run_launcher.resume_run_calls == 3

    # exausted the 3 attempts
    monitor_started_run(instance, workspace, run, logger)
    assert instance.get_run_by_id(run.run_id).status == DagsterRunStatus.FAILURE
    assert instance.run_launcher.launch_run_calls == 0
    assert instance.run_launcher.resume_run_calls == 3
