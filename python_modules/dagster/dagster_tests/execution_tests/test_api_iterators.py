import pytest

from dagster import GraphDefinition, op, _check as check
from dagster import resource
from dagster._core.definitions.pipeline_base import InMemoryPipeline
from dagster._core.errors import DagsterInvariantViolationError
from dagster._core.events.log import EventLogEntry, construct_event_logger
from dagster._core.execution.api import (
    create_execution_plan,
    execute_pipeline_iterator,
    execute_plan_iterator,
    execute_run,
    execute_run_iterator,
)
from dagster._core.storage.pipeline_run import PipelineRunStatus
from dagster._core.test_utils import instance_for_test


@resource
def resource_a(context):
    context.log.info("CALLING A")
    yield "A"
    context.log.info("CLEANING A")
    yield  # add the second yield here to test teardown generator exit handling


@resource
def resource_b(context):
    context.log.info("CALLING B")
    yield "B"
    context.log.info("CLEANING B")
    yield  # add the second yield here to test teardown generator exit handling


@op(required_resource_keys={"a", "b"})
def resource_op(_):
    return "A"


def test_execute_pipeline_iterator():
    with instance_for_test() as instance:
        records = []

        def event_callback(record):
            assert isinstance(record, EventLogEntry)
            records.append(record)

        pipeline = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        iterator = execute_pipeline_iterator(
            pipeline, run_config={"loggers": {"callback": {}}}, instance=instance
        )

        event_type = None
        while event_type != "STEP_START":
            event = next(iterator)
            event_type = event.event_type_value

        iterator.close()
        events = [record.dagster_event for record in records if record.is_dagster_event]
        messages = [
            record.user_message for record in records if not record.is_dagster_event
        ]
        pipeline_failure_events = [
            event for event in events if event.is_pipeline_failure
        ]
        assert len(pipeline_failure_events) == 1
        assert (
            "GeneratorExit"
            in pipeline_failure_events[0].pipeline_failure_data.error.message
        )
        assert len([message for message in messages if message == "CLEANING A"]) > 0
        assert len([message for message in messages if message == "CLEANING B"]) > 0


def test_execute_run_iterator():
    records = []

    def event_callback(record):
        assert isinstance(record, EventLogEntry)
        records.append(record)

    with instance_for_test() as instance:
        pipeline_def = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        )

        iterator = execute_run_iterator(
            InMemoryPipeline(pipeline_def), pipeline_run, instance=instance
        )

        event_type = None
        while event_type != "STEP_START":
            event = next(iterator)
            event_type = event.event_type_value

        iterator.close()
        events = [record.dagster_event for record in records if record.is_dagster_event]
        messages = [
            record.user_message for record in records if not record.is_dagster_event
        ]
        pipeline_failure_events = [
            event for event in events if event.is_pipeline_failure
        ]
        assert len(pipeline_failure_events) == 1
        assert (
            "GeneratorExit"
            in pipeline_failure_events[0].pipeline_failure_data.error.message
        )
        assert len([message for message in messages if message == "CLEANING A"]) > 0
        assert len([message for message in messages if message == "CLEANING B"]) > 0

        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.SUCCESS)

        events = list(
            execute_run_iterator(
                InMemoryPipeline(pipeline_def), pipeline_run, instance=instance
            )
        )

        assert any(
            [
                "Ignoring a run worker that started after the run had already finished."
                in event
                for event in events
            ]
        )

        with instance_for_test(
            overrides={
                "run_launcher": {
                    "module": "dagster_tests.daemon_tests.test_monitoring_daemon",
                    "class": "TestRunLauncher",
                },
                "run_monitoring": {"enabled": True},
            }
        ) as run_monitoring_instance:
            event = next(
                execute_run_iterator(
                    InMemoryPipeline(pipeline_def),
                    pipeline_run,
                    instance=run_monitoring_instance,
                )
            )
            assert (
                "Ignoring a duplicate run that was started from somewhere other than the run monitor daemon"
                in event.message
            )

            with pytest.raises(
                check.CheckError,
                match=r"in state DagsterRunStatus.SUCCESS, expected STARTED or STARTING "
                r"because it's resuming from a run worker failure",
            ):
                execute_run_iterator(
                    InMemoryPipeline(pipeline_def),
                    pipeline_run,
                    instance=run_monitoring_instance,
                    resume_from_failure=True,
                )

        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.CANCELED)

        events = list(
            execute_run_iterator(
                InMemoryPipeline(pipeline_def), pipeline_run, instance=instance
            )
        )

        assert len(events) == 1
        assert (
            events[0].message
            == "Not starting execution since the run was canceled before execution could start"
        )


def test_restart_running_run_worker():
    def event_callback(_record):
        pass

    with instance_for_test() as instance:
        pipeline_def = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.STARTED)

        events = list(
            execute_run_iterator(
                InMemoryPipeline(pipeline_def), pipeline_run, instance=instance
            )
        )

        assert any(
            [
                f"{pipeline_run.pipeline_name} ({pipeline_run.run_id}) started a new run worker while the run was already in state DagsterRunStatus.STARTED. "
                in event.message
                for event in events
            ]
        )

        assert (
            instance.get_run_by_id(pipeline_run.run_id).status
            == PipelineRunStatus.FAILURE
        )


def test_start_run_worker_after_run_failure():
    def event_callback(_record):
        pass

    with instance_for_test() as instance:
        pipeline_def = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.FAILURE)

        event = next(
            execute_run_iterator(
                InMemoryPipeline(pipeline_def), pipeline_run, instance=instance
            )
        )
        assert (
            "Ignoring a run worker that started after the run had already finished."
            in event.message
        )


def test_execute_canceled_state():
    def event_callback(_record):
        pass

    with instance_for_test() as instance:
        pipeline_def = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.CANCELED)

        with pytest.raises(DagsterInvariantViolationError):
            execute_run(
                InMemoryPipeline(pipeline_def),
                pipeline_run,
                instance=instance,
            )

        logs = instance.all_logs(pipeline_run.run_id)

        assert len(logs) == 1
        assert (
            "Not starting execution since the run was canceled before execution could start"
            in logs[0].message
        )

        iter_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.CANCELED)

        iter_events = list(
            execute_run_iterator(
                InMemoryPipeline(pipeline_def), iter_run, instance=instance
            )
        )

        assert len(iter_events) == 1
        assert (
            "Not starting execution since the run was canceled before execution could start"
            in iter_events[0].message
        )


def test_execute_run_bad_state():
    records = []

    def event_callback(record):
        assert isinstance(record, EventLogEntry)
        records.append(record)

    with instance_for_test() as instance:
        pipeline_def = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline_def,
            run_config={"loggers": {"callback": {}}},
            mode="default",
        ).with_status(PipelineRunStatus.SUCCESS)

        with pytest.raises(
            check.CheckError,
            match=r"Run basic_resource_pipeline \({}\) in state"
            r" DagsterRunStatus.SUCCESS, expected NOT_STARTED or STARTING".format(
                pipeline_run.run_id
            ),
        ):
            execute_run(InMemoryPipeline(pipeline_def), pipeline_run, instance=instance)


def test_execute_plan_iterator():
    records = []

    def event_callback(record):
        assert isinstance(record, EventLogEntry)
        records.append(record)

    with instance_for_test() as instance:
        pipeline = GraphDefinition(
            name="basic_resource_pipeline",
            node_defs=[resource_op],
        ).to_job(
            resource_defs={"a": resource_a, "b": resource_b},
            logger_defs={"callback": construct_event_logger(event_callback)},
        )
        run_config = {"loggers": {"callback": {}}}

        execution_plan = create_execution_plan(pipeline, run_config=run_config)
        pipeline_run = instance.create_run_for_pipeline(
            pipeline_def=pipeline,
            run_config={"loggers": {"callback": {}}},
            execution_plan=execution_plan,
        )

        iterator = execute_plan_iterator(
            execution_plan,
            InMemoryPipeline(pipeline),
            pipeline_run,
            instance,
            run_config=run_config,
        )

        event_type = None
        while event_type != "STEP_START":
            event = next(iterator)
            event_type = event.event_type_value

        iterator.close()
        messages = [
            record.user_message for record in records if not record.is_dagster_event
        ]
        assert len([message for message in messages if message == "CLEANING A"]) > 0
        assert len([message for message in messages if message == "CLEANING B"]) > 0
