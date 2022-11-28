import os
import tempfile
import time
import uuid
from threading import Thread
from typing import List

import pytest

from dagster import (
    AssetKey,
    AssetsDefinition,
    DynamicOut,
    DynamicOutput,
    Failure,
    Field,
    MetadataEntry,
    ResourceDefinition,
    RetryPolicy,
    RetryRequested,
    String,
    asset,
    define_asset_job,
    file_relative_path,
    fs_io_manager,
    job,
    op,
    reconstructable,
    repository,
    resource,
    with_resources,
)
from dagster._core.definitions.cacheable_assets import (
    AssetsDefinitionCacheableData,
    CacheableAssetsDefinition,
)
from dagster._core.definitions.no_step_launcher import no_step_launcher
from dagster._core.definitions.reconstruct import (
    ReconstructablePipeline,
    ReconstructableRepository,
)
from dagster._core.events import DagsterEventType
from dagster._core.execution.api import create_execution_plan
from dagster._core.execution.context_creation_pipeline import (
    PlanExecutionContextManager,
)
from dagster._core.execution.plan.external_step import (
    LocalExternalStepLauncher,
    local_external_step_launcher,
    step_context_to_step_run_ref,
    step_run_ref_to_step_context,
)
from dagster._core.execution.plan.state import KnownExecutionState
from dagster._core.execution.retries import RetryMode
from dagster._core.instance import DagsterInstance
from dagster._core.storage.pipeline_run import PipelineRun
from dagster._core.test_utils import instance_for_test
from dagster._legacy import (
    ModeDefinition,
    execute_pipeline_iterator,
    pipeline,
    reexecute_pipeline,
)
from dagster._utils import safe_tempfile_path, send_interrupt
from dagster._utils.merger import deep_merge_dicts, merge_dicts

RUN_CONFIG_BASE = {"solids": {"return_two": {"config": {"a": "b"}}}}


def make_run_config(scratch_dir, mode):
    if mode in ["external", "request_retry"]:
        step_launcher_resource_keys = ["first_step_launcher", "second_step_launcher"]
    else:
        step_launcher_resource_keys = ["second_step_launcher"]
    return deep_merge_dicts(
        RUN_CONFIG_BASE,
        {
            "resources": merge_dicts(
                {"io_manager": {"config": {"base_dir": scratch_dir}}},
                {
                    step_launcher_resource_key: {"config": {"scratch_dir": scratch_dir}}
                    for step_launcher_resource_key in step_launcher_resource_keys
                },
            ),
        },
    )


class RequestRetryLocalExternalStepLauncher(LocalExternalStepLauncher):
    def launch_step(self, step_context):
        if step_context.previous_attempt_count == 0:
            raise RetryRequested()
        else:
            return super(RequestRetryLocalExternalStepLauncher, self).launch_step(
                step_context
            )


@resource(config_schema=local_external_step_launcher.config_schema)
def request_retry_local_external_step_launcher(context):
    return RequestRetryLocalExternalStepLauncher(**context.resource_config)


def _define_failing_job(has_policy: bool, is_explicit: bool = True):
    @op(
        required_resource_keys={"step_launcher"},
        retry_policy=RetryPolicy(max_retries=3) if has_policy else None,
    )
    def retry_op(context):
        if context.retry_number < 3:
            if is_explicit:
                raise Failure(
                    description="some failure description", metadata={"foo": 1.23}
                )
            else:
                _ = "x" + 1
        return context.retry_number

    @job(
        resource_defs={
            "step_launcher": local_external_step_launcher,
            "io_manager": fs_io_manager,
        }
    )
    def retry_job():
        retry_op()

    return retry_job


def _define_retry_job():
    return _define_failing_job(has_policy=True)


def _define_error_job():
    return _define_failing_job(has_policy=False, is_explicit=False)


def _define_failure_job():
    return _define_failing_job(has_policy=False)


def _define_dynamic_job(launch_initial, launch_final):
    initial_launcher = (
        local_external_step_launcher
        if launch_initial
        else ResourceDefinition.mock_resource()
    )
    final_launcher = (
        local_external_step_launcher
        if launch_final
        else ResourceDefinition.mock_resource()
    )

    @op(required_resource_keys={"initial_launcher"}, out=DynamicOut(int))
    def dynamic_outs():
        for i in range(0, 3):
            yield DynamicOutput(value=i, mapping_key=f"num_{i}")

    @op
    def increment(i):
        return i + 1

    @op(required_resource_keys={"final_launcher"})
    def total(ins: List[int]):
        return sum(ins)

    @job(
        resource_defs={
            "initial_launcher": initial_launcher,
            "final_launcher": final_launcher,
            "io_manager": fs_io_manager,
        }
    )
    def my_job():
        all_incs = dynamic_outs().map(increment)
        total(all_incs.collect())

    return my_job


def _define_basic_job(launch_initial, launch_final):
    initial_launcher = (
        local_external_step_launcher
        if launch_initial
        else ResourceDefinition.mock_resource()
    )
    final_launcher = (
        local_external_step_launcher
        if launch_final
        else ResourceDefinition.mock_resource()
    )

    @op(required_resource_keys={"initial_launcher"})
    def op1():
        return 1

    @op(required_resource_keys={"initial_launcher"})
    def op2():
        return 2

    @op(required_resource_keys={"final_launcher"})
    def combine(a, b):
        return a + b

    @job(
        resource_defs={
            "initial_launcher": initial_launcher,
            "final_launcher": final_launcher,
            "io_manager": fs_io_manager,
        }
    )
    def my_job():
        combine(op1(), op2())

    return my_job


def define_dynamic_job_all_launched():
    return _define_dynamic_job(True, True)


def define_dynamic_job_first_launched():
    return _define_dynamic_job(True, False)


def define_dynamic_job_last_launched():
    return _define_dynamic_job(False, True)


def define_basic_job_all_launched():
    return _define_basic_job(True, True)


def define_basic_job_first_launched():
    return _define_basic_job(True, False)


def define_basic_job_last_launched():
    return _define_basic_job(False, True)


def define_basic_pipeline():
    @op(
        required_resource_keys=set(["first_step_launcher"]),
        config_schema={"a": Field(str)},
    )
    def return_two(_):
        return 2

    @op(required_resource_keys=set(["second_step_launcher"]))
    def add_one(_, num):
        return num + 1

    @pipeline(
        mode_defs=[
            ModeDefinition(
                "external",
                resource_defs={
                    "first_step_launcher": local_external_step_launcher,
                    "second_step_launcher": local_external_step_launcher,
                    "io_manager": fs_io_manager,
                },
            ),
            ModeDefinition(
                "internal_and_external",
                resource_defs={
                    "first_step_launcher": no_step_launcher,
                    "second_step_launcher": local_external_step_launcher,
                    "io_manager": fs_io_manager,
                },
            ),
            ModeDefinition(
                "request_retry",
                resource_defs={
                    "first_step_launcher": request_retry_local_external_step_launcher,
                    "second_step_launcher": request_retry_local_external_step_launcher,
                    "io_manager": fs_io_manager,
                },
            ),
        ]
    )
    def basic_job():
        add_one(return_two())

    return basic_job


def define_sleepy_pipeline():
    @op(
        config_schema={"tempfile": Field(String)},
        required_resource_keys=set(["first_step_launcher"]),
    )
    def sleepy_op(context):
        with open(context.op_config["tempfile"], "w", encoding="utf8") as ff:
            ff.write("yup")
        start_time = time.time()
        while True:
            time.sleep(0.1)
            if time.time() - start_time > 120:
                raise Exception("Timed out")

    @job(
        resource_defs={
            "first_step_launcher": local_external_step_launcher,
            "io_manager": fs_io_manager,
        }
    )
    def sleepy_job():
        sleepy_op()

    return sleepy_job


def initialize_step_context(scratch_dir, instance):
    pipeline_run = PipelineRun(
        pipeline_name="foo_pipeline",
        run_id=str(uuid.uuid4()),
        run_config=make_run_config(scratch_dir, "external"),
        mode="external",
    )

    recon_pipeline = reconstructable(define_basic_pipeline)

    plan = create_execution_plan(
        recon_pipeline, pipeline_run.run_config, mode="external"
    )

    initialization_manager = PlanExecutionContextManager(
        pipeline=recon_pipeline,
        execution_plan=plan,
        run_config=pipeline_run.run_config,
        pipeline_run=pipeline_run,
        instance=instance,
        retry_mode=RetryMode.DISABLED,
    )
    for _ in initialization_manager.prepare_context():
        pass
    pipeline_context = initialization_manager.get_context()

    step_context = pipeline_context.for_step(
        plan.get_step_by_key("return_two"),
        KnownExecutionState(),
    )
    return step_context


def test_step_context_to_step_run_ref():
    with DagsterInstance.ephemeral() as instance:
        step_context = initialize_step_context("", instance)
        step = step_context.step
        step_run_ref = step_context_to_step_run_ref(step_context)
        assert step_run_ref.run_config == step_context.pipeline_run.run_config
        assert step_run_ref.run_id == step_context.pipeline_run.run_id

        rehydrated_step_context = step_run_ref_to_step_context(step_run_ref, instance)
        rehydrated_step = rehydrated_step_context.step
        assert rehydrated_step.pipeline_name == step.pipeline_name
        assert rehydrated_step.step_inputs == step.step_inputs
        assert rehydrated_step.step_outputs == step.step_outputs
        assert rehydrated_step.kind == step.kind
        assert rehydrated_step.solid_handle.name == step.solid_handle.name
        assert rehydrated_step.logging_tags == step.logging_tags
        assert rehydrated_step.tags == step.tags


def test_local_external_step_launcher():
    with tempfile.TemporaryDirectory() as tmpdir:
        with DagsterInstance.ephemeral() as instance:
            step_context = initialize_step_context(tmpdir, instance)

            step_launcher = LocalExternalStepLauncher(tmpdir)
            events = list(step_launcher.launch_step(step_context))
            event_types = [event.event_type for event in events]
            assert DagsterEventType.STEP_START in event_types
            assert DagsterEventType.STEP_SUCCESS in event_types
            assert DagsterEventType.STEP_FAILURE not in event_types


@pytest.mark.parametrize("mode", ["external", "internal_and_external"])
def test_pipeline(mode):
    with tempfile.TemporaryDirectory() as tmpdir:
        result = reconstructable(define_basic_pipeline).execute_in_process(
            mode=mode,
            run_config=make_run_config(tmpdir, mode),
        )
        assert result.result_for_solid("return_two").output_value() == 2
        assert result.result_for_solid("add_one").output_value() == 3


@pytest.mark.parametrize(
    "job_fn",
    [
        define_dynamic_job_all_launched,
        define_dynamic_job_first_launched,
        define_dynamic_job_last_launched,
    ],
)
def test_dynamic_job(job_fn):
    with tempfile.TemporaryDirectory() as tmpdir:
        with instance_for_test() as instance:
            result = reconstructable(job_fn).execute_in_process(
                run_config={
                    "resources": {
                        "initial_launcher": {
                            "config": {"scratch_dir": tmpdir},
                        },
                        "final_launcher": {
                            "config": {"scratch_dir": tmpdir},
                        },
                        "io_manager": {"config": {"base_dir": tmpdir}},
                    }
                },
                instance=instance,
            )
            assert result.output_for_solid("total") == 6


@pytest.mark.parametrize(
    "job_fn",
    [
        define_basic_job_all_launched,
        define_basic_job_first_launched,
        define_basic_job_last_launched,
    ],
)
def test_reexecution(job_fn):
    with tempfile.TemporaryDirectory() as tmpdir:
        run_config = {
            "resources": {
                "initial_launcher": {
                    "config": {"scratch_dir": tmpdir},
                },
                "final_launcher": {
                    "config": {"scratch_dir": tmpdir},
                },
                "io_manager": {"config": {"base_dir": tmpdir}},
            }
        }
        with instance_for_test() as instance:
            run1 = reconstructable(job_fn).execute_in_process(
                run_config=run_config,
                instance=instance,
            )
            assert run1.success
            assert run1.result_for_solid("combine").output_value() == 3
            run2 = reexecute_pipeline(
                pipeline=reconstructable(job_fn),
                parent_run_id=run1.run_id,
                run_config=run_config,
                instance=instance,
                step_selection=["combine"],
            )
            assert run2.success
            assert run2.result_for_solid("combine").output_value() == 3


def test_retry_policy():
    with tempfile.TemporaryDirectory() as tmpdir:
        run_config = {
            "resources": {
                "step_launcher": {"config": {"scratch_dir": tmpdir}},
                "io_manager": {"config": {"base_dir": tmpdir}},
            }
        }
        with instance_for_test() as instance:
            run = reconstructable(_define_retry_job).execute_in_process(
                run_config=run_config,
                instance=instance,
            )
            assert run.success
            assert run.result_for_solid("retry_op").output_value() == 3
            step_retry_events = [
                e for e in run.event_list if e.event_type_value == "STEP_RESTARTED"
            ]
            assert len(step_retry_events) == 3


def test_explicit_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        run_config = {
            "resources": {
                "step_launcher": {"config": {"scratch_dir": tmpdir}},
                "io_manager": {"config": {"base_dir": tmpdir}},
            }
        }
        with instance_for_test() as instance:
            run = reconstructable(_define_failure_job).execute_in_process(
                run_config=run_config,
                instance=instance,
                raise_on_error=False,
            )
            fd = run.result_for_solid("retry_op").failure_data
            assert fd.user_failure_data.description == "some failure description"
            assert fd.user_failure_data.metadata_entries == [
                MetadataEntry.float(label="foo", value=1.23)
            ]


def test_arbitrary_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        run_config = {
            "resources": {
                "step_launcher": {"config": {"scratch_dir": tmpdir}},
                "io_manager": {"config": {"base_dir": tmpdir}},
            }
        }
        with instance_for_test() as instance:
            run = reconstructable(_define_error_job).execute_in_process(
                run_config=run_config,
                instance=instance,
                raise_on_error=False,
            )
            failure_events = [
                e for e in run.event_list if e.event_type_value == "STEP_FAILURE"
            ]
            assert len(failure_events) == 1
            fd = run.result_for_solid("retry_op").failure_data
            assert fd.error.cause.cls_name == "TypeError"


def test_launcher_requests_retry():
    mode = "request_retry"
    with tempfile.TemporaryDirectory() as tmpdir:
        result = reconstructable(define_basic_pipeline).execute_in_process(
            mode=mode,
            run_config=make_run_config(tmpdir, mode),
        )
        assert result.success
        assert result.result_for_solid("return_two").output_value() == 2
        assert result.result_for_solid("add_one").output_value() == 3
        for step_key, events in result.events_by_step_key.items():
            if step_key:
                event_types = [event.event_type for event in events]
                assert DagsterEventType.STEP_UP_FOR_RETRY in event_types
                assert DagsterEventType.STEP_RESTARTED in event_types


def _send_interrupt_thread(temp_file):
    while not os.path.exists(temp_file):
        time.sleep(0.1)
    send_interrupt()


@pytest.mark.parametrize("mode", ["external"])
def test_interrupt_step_launcher(mode):
    with tempfile.TemporaryDirectory() as tmpdir:
        with safe_tempfile_path() as success_tempfile:
            sleepy_run_config = {
                "resources": {
                    "first_step_launcher": {
                        "config": {"scratch_dir": tmpdir},
                    },
                    "io_manager": {"config": {"base_dir": tmpdir}},
                },
                "solids": {"sleepy_op": {"config": {"tempfile": success_tempfile}}},
            }

            interrupt_thread = Thread(
                target=_send_interrupt_thread, args=(success_tempfile,)
            )

            interrupt_thread.start()

            results = []

            for result in execute_pipeline_iterator(
                pipeline=reconstructable(define_sleepy_pipeline),
                mode=mode,
                run_config=sleepy_run_config,
            ):
                results.append(result.event_type)

            assert DagsterEventType.STEP_FAILURE in results
            assert DagsterEventType.PIPELINE_FAILURE in results

            interrupt_thread.join()


def test_multiproc_launcher_requests_retry():
    mode = "request_retry"
    with tempfile.TemporaryDirectory() as tmpdir:
        run_config = make_run_config(tmpdir, mode)
        run_config["execution"] = {"multiprocess": {}}
        result = DagsterInstance.local_temp(tmpdir).execute_in_process(
            pipeline=reconstructable(define_basic_pipeline),
            mode=mode,
            run_config=run_config,
        )
        assert result.success
        assert result.result_for_solid("return_two").output_value() == 2
        assert result.result_for_solid("add_one").output_value() == 3
        for step_key, events in result.events_by_step_key.items():
            if step_key:
                event_types = [event.event_type for event in events]
                assert DagsterEventType.STEP_UP_FOR_RETRY in event_types
                assert DagsterEventType.STEP_RESTARTED in event_types


def test_multiproc_launcher_with_repository_load_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        run_config = {
            "resources": {
                "step_launcher": {"config": {"scratch_dir": tmpdir}},
                "io_manager": {"config": {"base_dir": tmpdir}},
            }
        }
        with instance_for_test() as instance:
            instance.run_storage.kvs_set({"val": "INITIAL_VALUE"})
            recon_repo = ReconstructableRepository.for_file(
                file_relative_path(__file__, "test_external_step.py"),
                fn_name="pending_repo",
            )
            recon_pipeline = ReconstructablePipeline(
                repository=recon_repo, pipeline_name="all_asset_job"
            )

            run = recon_pipeline.execute_in_process(
                run_config=run_config,
                instance=instance,
            )
            assert run.success
            assert instance.run_storage.kvs_get({"val"}).get("val") == "NEW_VALUE"


class MyCacheableAssetsDefinition(CacheableAssetsDefinition):
    _cacheable_data = AssetsDefinitionCacheableData(
        keys_by_output_name={"result": AssetKey("foo")}
    )

    def compute_cacheable_data(self):
        # used for tracking how many times this function gets called over an execution
        # since we're crossing process boundaries, we pre-populate this value in the host process
        # and assert that this pre-populated value is present, to ensure that we'll error if this
        # gets called in a child process
        instance = DagsterInstance.get()
        val = instance.run_storage.kvs_get({"val"}).get("val")
        assert val == "INITIAL_VALUE"
        instance.run_storage.kvs_set({"val": "NEW_VALUE"})
        return [self._cacheable_data]

    def build_definitions(self, data):
        assert len(data) == 1
        assert data == [self._cacheable_data]

        @op(required_resource_keys={"step_launcher"})
        def _op():
            return 1

        return with_resources(
            [
                AssetsDefinition.from_op(
                    _op,
                    keys_by_output_name=cd.keys_by_output_name,
                )
                for cd in data
            ],
            {"step_launcher": local_external_step_launcher},
        )


@asset
def bar(foo):
    return foo + 1


@repository
def pending_repo():
    return [bar, MyCacheableAssetsDefinition("xyz"), define_asset_job("all_asset_job")]
