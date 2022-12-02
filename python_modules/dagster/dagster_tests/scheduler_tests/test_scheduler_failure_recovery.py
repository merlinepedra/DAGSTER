import multiprocessing

import pendulum
import pytest

from dagster._core.instance import DagsterInstance
from dagster._core.scheduler.instigation import TickStatus
from dagster._core.storage.pipeline_run import DagsterRunStatus
from dagster._core.storage.tags import PARTITION_NAME_TAG, SCHEDULED_EXECUTION_TIME_TAG
from dagster._core.test_utils import (
    SingleThreadPoolExecutor,
    cleanup_test_instance,
    create_test_daemon_workspace_context,
    get_crash_signals,
    get_terminate_signal,
)
from dagster._seven import IS_WINDOWS
from dagster._seven.compat.pendulum import create_pendulum_time, to_timezone

from .conftest import workspace_load_target
from .test_scheduler_run import (
    evaluate_schedules,
    validate_run_exists,
    validate_tick,
    wait_for_all_runs_to_start,
)

spawn_ctx = multiprocessing.get_context("spawn")


def get_schedule_executor_names():
    return [
        pytest.param(
            "multi",
            id="synchronous",
        ),
        pytest.param(
            "single",
            id="threadpool",
        ),
    ]


def _test_launch_scheduled_runs_in_subprocess(
    instance_ref, execution_datetime, debug_crash_flags, executor_name
):
    executor = SingleThreadPoolExecutor() if executor_name == "single" else None
    with DagsterInstance.from_ref(instance_ref) as instance:
        try:
            with create_test_daemon_workspace_context(
                workspace_load_target(), instance
            ) as workspace_context:
                with pendulum.test(execution_datetime):
                    evaluate_schedules(
                        workspace_context,
                        executor,
                        pendulum.now("UTC"),
                        debug_crash_flags=debug_crash_flags,
                    )
        finally:
            cleanup_test_instance(instance)


@pytest.mark.skipif(
    IS_WINDOWS, reason="Windows keeps resources open after termination in a flaky way"
)
@pytest.mark.parametrize("crash_location", ["TICK_CREATED", "TICK_HELD"])
@pytest.mark.parametrize("crash_signal", get_crash_signals())
@pytest.mark.parametrize("executor", get_schedule_executor_names())
def test_failure_recovery_before_run_created(
    instance, external_repo, crash_location, crash_signal, executor
):
    # Verify that if the scheduler crashes or is interrupted before a run is created,
    # it will create exactly one tick/run when it is re-launched
    initial_datetime = to_timezone(
        create_pendulum_time(year=2019, month=2, day=27, hour=0, minute=0, second=0, tz="UTC"),
        "US/Central",
    )

    frozen_datetime = initial_datetime.add()

    external_schedule = external_repo.get_external_schedule("simple_schedule")
    with pendulum.test(frozen_datetime):
        instance.start_schedule(external_schedule)

        debug_crash_flags = {external_schedule.name: {crash_location: crash_signal}}

        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, debug_crash_flags, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)

        assert scheduler_process.exitcode != 0

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1
        assert ticks[0].status == TickStatus.STARTED

        assert instance.get_runs_count() == 0

    frozen_datetime = frozen_datetime.add(minutes=5)
    with pendulum.test(frozen_datetime):
        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, None, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)
        assert scheduler_process.exitcode == 0

        assert instance.get_runs_count() == 1
        wait_for_all_runs_to_start(instance)
        validate_run_exists(
            instance.get_runs()[0],
            execution_time=initial_datetime,
            partition_time=create_pendulum_time(2019, 2, 26),
        )

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1
        validate_tick(
            ticks[0],
            external_schedule,
            initial_datetime,
            TickStatus.SUCCESS,
            [instance.get_runs()[0].run_id],
        )


@pytest.mark.skipif(
    IS_WINDOWS, reason="Windows keeps resources open after termination in a flaky way"
)
@pytest.mark.parametrize("crash_location", ["RUN_CREATED", "RUN_LAUNCHED"])
@pytest.mark.parametrize("crash_signal", get_crash_signals())
@pytest.mark.parametrize("executor", get_schedule_executor_names())
def test_failure_recovery_after_run_created(
    instance, external_repo, crash_location, crash_signal, executor
):
    # Verify that if the scheduler crashes or is interrupted after a run is created,
    # it will just re-launch the already-created run when it runs again
    initial_datetime = create_pendulum_time(year=2019, month=2, day=27, hour=0, minute=0, second=0)
    frozen_datetime = initial_datetime.add()
    external_schedule = external_repo.get_external_schedule("simple_schedule")
    with pendulum.test(frozen_datetime):
        instance.start_schedule(external_schedule)

        debug_crash_flags = {external_schedule.name: {crash_location: crash_signal}}

        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, debug_crash_flags, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)

        assert scheduler_process.exitcode != 0

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1
        assert ticks[0].status == TickStatus.STARTED

        assert instance.get_runs_count() == 1

        if crash_location == "RUN_CREATED":
            run = instance.get_runs()[0]
            # Run was created, but hasn't launched yet
            assert run.tags[SCHEDULED_EXECUTION_TIME_TAG] == frozen_datetime.isoformat()
            assert run.tags[PARTITION_NAME_TAG] == "2019-02-26"
            assert run.status == DagsterRunStatus.NOT_STARTED
        else:
            # The run was created and launched - running again should do nothing other than
            # moving the tick to success state.

            # The fact that we need to add this line indicates that there is still a theoretical
            # possible race condition - if the scheduler fails after launching a run
            # and then runs again between when the run was launched and when its status is changed to STARTED by the executor, we could
            # end up launching the same run twice. Run queueing or some other way to immediately
            # identify that a run was launched would help eliminate this race condition. For now,
            # eliminate the possibility by waiting for the run to start before running the
            # scheduler again.
            wait_for_all_runs_to_start(instance)

            run = instance.get_runs()[0]
            validate_run_exists(
                instance.get_runs()[0], frozen_datetime, create_pendulum_time(2019, 2, 26)
            )

    frozen_datetime = frozen_datetime.add(minutes=5)
    with pendulum.test(frozen_datetime):

        # Running again just launches the existing run and marks the tick as success
        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, None, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)
        assert scheduler_process.exitcode == 0

        assert instance.get_runs_count() == 1
        wait_for_all_runs_to_start(instance)
        validate_run_exists(
            instance.get_runs()[0], initial_datetime, create_pendulum_time(2019, 2, 26)
        )

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1
        validate_tick(
            ticks[0],
            external_schedule,
            initial_datetime,
            TickStatus.SUCCESS,
            [instance.get_runs()[0].run_id],
        )


@pytest.mark.skipif(
    IS_WINDOWS, reason="Windows keeps resources open after termination in a flaky way"
)
@pytest.mark.parametrize("crash_location", ["TICK_SUCCESS"])
@pytest.mark.parametrize("crash_signal", get_crash_signals())
@pytest.mark.parametrize("executor", get_schedule_executor_names())
def test_failure_recovery_after_tick_success(
    instance, external_repo, crash_location, crash_signal, executor
):
    initial_datetime = create_pendulum_time(year=2019, month=2, day=27, hour=0, minute=0, second=0)
    frozen_datetime = initial_datetime.add()
    external_schedule = external_repo.get_external_schedule("simple_schedule")
    with pendulum.test(frozen_datetime):
        instance.start_schedule(external_schedule)

        debug_crash_flags = {external_schedule.name: {crash_location: crash_signal}}

        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, debug_crash_flags, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)

        assert scheduler_process.exitcode != 0

        # As above there's a possible race condition here if the scheduler crashes
        # and launches the same run twice if we crash right after the launch and re-run
        # before the run actually starts
        wait_for_all_runs_to_start(instance)

        assert instance.get_runs_count() == 1
        validate_run_exists(
            instance.get_runs()[0], initial_datetime, create_pendulum_time(2019, 2, 26)
        )

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1

        if crash_signal == get_terminate_signal():
            run_ids = []
        else:
            run_ids = [run.run_id for run in instance.get_runs()]

        validate_tick(
            ticks[0],
            external_schedule,
            initial_datetime,
            TickStatus.STARTED,
            run_ids,
        )

    frozen_datetime = frozen_datetime.add(minutes=1)
    with pendulum.test(frozen_datetime):
        # Running again just marks the tick as success since the run has already started
        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, None, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)
        assert scheduler_process.exitcode == 0

        assert instance.get_runs_count() == 1
        validate_run_exists(
            instance.get_runs()[0], initial_datetime, create_pendulum_time(2019, 2, 26)
        )

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1
        validate_tick(
            ticks[0],
            external_schedule,
            initial_datetime,
            TickStatus.SUCCESS,
            [instance.get_runs()[0].run_id],
        )


@pytest.mark.skipif(
    IS_WINDOWS, reason="Windows keeps resources open after termination in a flaky way"
)
@pytest.mark.parametrize("crash_location", ["RUN_ADDED"])
@pytest.mark.parametrize("crash_signal", get_crash_signals())
@pytest.mark.parametrize("executor", get_schedule_executor_names())
def test_failure_recovery_between_multi_runs(
    instance, external_repo, crash_location, crash_signal, executor
):
    initial_datetime = create_pendulum_time(year=2019, month=2, day=28, hour=0, minute=0, second=0)
    frozen_datetime = initial_datetime.add()
    external_schedule = external_repo.get_external_schedule("multi_run_schedule")
    with pendulum.test(frozen_datetime):
        instance.start_schedule(external_schedule)

        debug_crash_flags = {external_schedule.name: {crash_location: crash_signal}}

        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, debug_crash_flags, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)

        assert scheduler_process.exitcode != 0

        wait_for_all_runs_to_start(instance)
        assert instance.get_runs_count() == 1
        validate_run_exists(instance.get_runs()[0], initial_datetime)

        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1

    frozen_datetime = frozen_datetime.add(minutes=1)
    with pendulum.test(frozen_datetime):
        scheduler_process = spawn_ctx.Process(
            target=_test_launch_scheduled_runs_in_subprocess,
            args=[instance.get_ref(), frozen_datetime, None, executor],
        )
        scheduler_process.start()
        scheduler_process.join(timeout=60)
        assert scheduler_process.exitcode == 0
        assert instance.get_runs_count() == 2
        validate_run_exists(instance.get_runs()[0], initial_datetime)
        ticks = instance.get_ticks(
            external_schedule.get_external_origin_id(), external_schedule.selector_id
        )
        assert len(ticks) == 1
        validate_tick(
            ticks[0],
            external_schedule,
            initial_datetime,
            TickStatus.SUCCESS,
            [run.run_id for run in instance.get_runs()],
        )
