import os
import shutil
import tempfile
import uuid
from collections import defaultdict
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Any,
    DefaultDict,
    Dict,
    Generator,
    Mapping,
    Optional,
    Union,
    overload,
)

# top-level include is dangerous in terms of incurring circular deps
from dagster import (
    DagsterInvariantViolationError,
    DependencyDefinition,
    Failure,
    NodeInvocation,
    RepositoryDefinition,
    TypeCheck,
)
from dagster import _check as check
from dagster._core.definitions import (
    GraphDefinition,
    GraphIn,
    GraphOut,
    InputMapping,
    JobDefinition,
    ModeDefinition,
    OpDefinition,
    OutputMapping,
    ResourceDefinition,
    lambda_solid,
)
from dagster._core.definitions.dependency import IDependencyDefinition
from dagster._core.definitions.logger_definition import LoggerDefinition
from dagster._core.definitions.node_definition import NodeDefinition
from dagster._core.definitions.pipeline_base import InMemoryPipeline
from dagster._core.definitions.resource_definition import ScopedResourcesBuilder
from dagster._core.execution.api import (
    create_execution_plan,
    execute_pipeline,
    scoped_pipeline_context,
)
from dagster._core.execution.context.system import PlanExecutionContext
from dagster._core.execution.context_creation_pipeline import (
    create_context_creation_data,
    create_execution_data,
    create_executor,
    create_log_manager,
    create_plan_data,
)
from dagster._core.execution.execute_in_process_result import ExecuteInProcessResult
from dagster._core.instance import DagsterInstance
from dagster._core.scheduler import Scheduler
from dagster._core.scheduler.scheduler import DagsterScheduleDoesNotExist, DagsterSchedulerError
from dagster._core.snap import snapshot_from_execution_plan
from dagster._core.storage.file_manager import LocalFileManager
from dagster._core.storage.pipeline_run import DagsterRun
from dagster._core.types.dagster_type import DagsterType, resolve_dagster_type
from dagster._core.utility_solids import define_stub_solid
from dagster._core.utils import make_new_run_id
from dagster._serdes import ConfigurableClass

# pylint: disable=unused-import
from ..temp_file import (
    get_temp_dir,
    get_temp_file_handle,
    get_temp_file_handle_with_data,
    get_temp_file_name,
    get_temp_file_name_with_data,
    get_temp_file_names,
)
from ..typing_api import is_typing_type

if TYPE_CHECKING:
    from dagster._core.execution.results import CompositeSolidExecutionResult, OpExecutionResult


def create_test_pipeline_execution_context(
    logger_defs: Optional[Mapping[str, LoggerDefinition]] = None
) -> PlanExecutionContext:
    loggers = check.opt_mapping_param(
        logger_defs, "logger_defs", key_type=str, value_type=LoggerDefinition
    )
    mode_def = ModeDefinition(logger_defs=loggers)
    my_graph = GraphDefinition(name="test_graph", node_defs=[])
    pipeline_def = JobDefinition(
        name="test_legacy_context",
        graph_def=my_graph,
        _mode_def=mode_def,
    )
    run_config: Dict[str, Dict[str, Dict]] = {"loggers": {key: {} for key in loggers}}
    pipeline_run = DagsterRun(pipeline_name="test_legacy_context", run_config=run_config)
    instance = DagsterInstance.ephemeral()
    execution_plan = create_execution_plan(pipeline=pipeline_def, run_config=run_config)
    creation_data = create_context_creation_data(
        InMemoryPipeline(pipeline_def),
        execution_plan,
        run_config,
        pipeline_run,
        instance,
    )
    log_manager = create_log_manager(creation_data)
    scoped_resources_builder = ScopedResourcesBuilder()
    executor = create_executor(creation_data)

    return PlanExecutionContext(
        plan_data=create_plan_data(creation_data, True, executor.retries),
        execution_data=create_execution_data(
            context_creation_data=creation_data,
            scoped_resources_builder=scoped_resources_builder,
        ),
        log_manager=log_manager,
        output_capture=None,
    )


def _dep_key_of(solid):
    return NodeInvocation(solid.definition.name, solid.name)


def build_pipeline_with_input_stubs(
    pipeline_def: JobDefinition, inputs: Mapping[str, Mapping[str, object]]
) -> JobDefinition:
    check.inst_param(pipeline_def, "pipeline_def", JobDefinition)
    check.mapping_param(inputs, "inputs", key_type=str, value_type=dict)

    deps: Dict[str, Dict[str, object]] = defaultdict(dict)
    for solid_name, dep_dict in pipeline_def.dependencies.items():
        for input_name, dep in dep_dict.items():
            deps[solid_name][input_name] = dep  # type: ignore

    stub_solid_defs = []

    for solid_name, input_dict in inputs.items():
        if not pipeline_def.has_solid_named(solid_name):
            raise DagsterInvariantViolationError(
                (
                    "You are injecting an input value for solid {solid_name} "
                    "into pipeline {pipeline_name} but that solid was not found"
                ).format(solid_name=solid_name, pipeline_name=pipeline_def.name)
            )

        solid = pipeline_def.solid_named(solid_name)
        for input_name, input_value in input_dict.items():
            stub_solid_def = define_stub_solid(
                "__stub_{solid_name}_{input_name}".format(
                    solid_name=solid_name, input_name=input_name
                ),
                input_value,
            )
            stub_solid_defs.append(stub_solid_def)
            deps[_dep_key_of(solid)][input_name] = DependencyDefinition(stub_solid_def.name)  # type: ignore

    return JobDefinition(
        name=pipeline_def.name + "_stubbed",
        solid_defs=[*pipeline_def.top_level_solid_defs, *stub_solid_defs],
        mode_defs=pipeline_def.mode_definitions,
        dependencies=deps,  # type: ignore
    )


def execute_solids_within_pipeline(
    pipeline_def: JobDefinition,
    solid_names: AbstractSet[str],
    inputs: Optional[Mapping[str, Mapping[str, object]]] = None,
    run_config: Optional[Mapping[str, object]] = None,
    mode: Optional[str] = None,
    preset: Optional[str] = None,
    tags: Optional[Mapping[str, str]] = None,
    instance: Optional[DagsterInstance] = None,
) -> Mapping[str, Union["CompositeSolidExecutionResult", "OpExecutionResult"]]:
    """Execute a set of solids within an existing pipeline.

    Intended to support tests. Input values may be passed directly.

    Args:
        pipeline_def (PipelineDefinition): The pipeline within which to execute the solid.
        solid_names (FrozenSet[str]): A set of the solid names, or the aliased solids, to execute.
        inputs (Optional[Dict[str, Dict[str, Any]]]): A dict keyed on solid names, whose values are
            dicts of input names to input values, used to pass input values to the solids directly.
            You may also use the ``run_config`` to configure any inputs that are configurable.
        run_config (Optional[dict]): The configuration that parameterized this
            execution, as a dict.
        mode (Optional[str]): The name of the pipeline mode to use. You may not set both ``mode``
            and ``preset``.
        preset (Optional[str]): The name of the pipeline preset to use. You may not set both
            ``mode`` and ``preset``.
        tags (Optional[Dict[str, Any]]): Arbitrary key-value pairs that will be added to pipeline
            logs.
        instance (Optional[DagsterInstance]): The instance to execute against. If this is ``None``,
            an ephemeral instance will be used, and no artifacts will be persisted from the run.

    Returns:
        Dict[str, Union[CompositeSolidExecutionResult, SolidExecutionResult]]: The results of
        executing the solids, keyed by solid name.
    """
    check.inst_param(pipeline_def, "pipeline_def", JobDefinition)
    check.set_param(solid_names, "solid_names", of_type=str)
    inputs = check.opt_mapping_param(inputs, "inputs", key_type=str, value_type=dict)

    sub_pipeline = pipeline_def.get_job_def_for_subset_selection(list(solid_names))
    stubbed_pipeline = build_pipeline_with_input_stubs(sub_pipeline, inputs)
    result = execute_pipeline(
        stubbed_pipeline,
        run_config=run_config,
        mode=mode,
        preset=preset,
        tags=tags,
        instance=instance,
    )

    return {sr.solid.name: sr for sr in result.solid_result_list}


def wrap_op_in_graph(
    op_def: OpDefinition, tags: Optional[Mapping[str, Any]] = None
) -> GraphDefinition:
    """Wraps op in a graph with the same inputs/outputs as the original op."""
    check.inst_param(op_def, "op_def", OpDefinition)
    check.opt_mapping_param(tags, "tags", key_type=str)

    input_mappings = []
    for input_name in op_def.ins.keys():
        # create an input mapping to the inner node with the same name.
        input_mappings.append(
            InputMapping(
                graph_input_name=input_name,
                mapped_node_name=op_def.name,
                mapped_node_input_name=input_name,
            )
        )

    output_mappings = []
    for output_name in op_def.outs.keys():
        out = op_def.outs[output_name]
        output_mappings.append(
            OutputMapping(
                graph_output_name=output_name,
                mapped_node_name=op_def.name,
                mapped_node_output_name=output_name,
                from_dynamic_mapping=out.is_dynamic,
            )
        )
    return GraphDefinition(
        name=f"wraps_{op_def.name}",
        node_defs=[op_def],
        input_mappings=input_mappings,
        output_mappings=output_mappings,
        tags=tags,
    )


def wrap_op_in_graph_and_execute(
    op_def: OpDefinition,
    resources: Optional[Mapping[str, Any]] = None,
    input_values: Optional[Mapping[str, Any]] = None,
    tags: Optional[Mapping[str, Any]] = None,
    run_config: Optional[Mapping[str, object]] = None,
    raise_on_error: bool = True,
    logger_defs: Optional[Mapping[str, LoggerDefinition]] = None,
) -> ExecuteInProcessResult:
    """Run a dagster op in an actual execution.
    For internal use."""
    return wrap_op_in_graph(op_def, tags).execute_in_process(
        resources=resources,
        input_values=input_values,
        raise_on_error=raise_on_error,
        run_config=run_config,
        logger_defs=logger_defs,
    )


def execute_solid_within_pipeline(
    pipeline_def: JobDefinition,
    solid_name: str,
    inputs: Optional[Dict[str, object]] = None,
    run_config: Optional[Dict[str, object]] = None,
    mode: Optional[str] = None,
    preset: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
    instance: Optional[DagsterInstance] = None,
) -> Union["CompositeSolidExecutionResult", "OpExecutionResult"]:

    """Execute a single solid within an existing pipeline.

    Intended to support tests. Input values may be passed directly.

    Args:
        pipeline_def (PipelineDefinition): The pipeline within which to execute the solid.
        solid_name (str): The name of the solid, or the aliased solid, to execute.
        inputs (Optional[Dict[str, Any]]): A dict of input names to input values, used to
            pass input values to the solid directly. You may also use the ``run_config`` to
            configure any inputs that are configurable.
        run_config (Optional[dict]): The configuration that parameterized this
            execution, as a dict.
        mode (Optional[str]): The name of the pipeline mode to use. You may not set both ``mode``
            and ``preset``.
        preset (Optional[str]): The name of the pipeline preset to use. You may not set both
            ``mode`` and ``preset``.
        tags (Optional[Dict[str, Any]]): Arbitrary key-value pairs that will be added to pipeline
            logs.
        instance (Optional[DagsterInstance]): The instance to execute against. If this is ``None``,
            an ephemeral instance will be used, and no artifacts will be persisted from the run.

    Returns:
        Union[CompositeSolidExecutionResult, SolidExecutionResult]: The result of executing the
        solid.
    """

    return execute_solids_within_pipeline(
        pipeline_def,
        solid_names={solid_name},
        inputs={solid_name: inputs} if inputs else None,
        run_config=run_config,
        mode=mode,
        preset=preset,
        tags=tags,
        instance=instance,
    )[solid_name]


@overload
def execute_solid(
    op_def: GraphDefinition,
    mode_def: Optional[ModeDefinition] = ...,
    input_values: Optional[Mapping[str, object]] = ...,
    tags: Optional[Mapping[str, Any]] = ...,
    run_config: Optional[Mapping[str, object]] = ...,
    raise_on_error: bool = ...,
) -> "CompositeSolidExecutionResult":
    ...


@overload
def execute_solid(
    op_def: OpDefinition,
    mode_def: Optional[ModeDefinition] = ...,
    input_values: Optional[Mapping[str, object]] = ...,
    tags: Optional[Mapping[str, Any]] = ...,
    run_config: Optional[Mapping[str, object]] = ...,
    raise_on_error: bool = ...,
) -> "OpExecutionResult":
    ...


def execute_solid(
    op_def: NodeDefinition,
    mode_def: Optional[ModeDefinition] = None,
    input_values: Optional[Mapping[str, object]] = None,
    tags: Optional[Mapping[str, Any]] = None,
    run_config: Optional[Mapping[str, object]] = None,
    raise_on_error: bool = True,
) -> Union["CompositeSolidExecutionResult", "OpExecutionResult"]:
    """Execute a single solid in an ephemeral pipeline.

    Intended to support unit tests. Input values may be passed directly, and no pipeline need be
    specified -- an ephemeral pipeline will be constructed.

    Args:
        solid_def (SolidDefinition): The solid to execute.
        mode_def (Optional[ModeDefinition]): The mode within which to execute the solid. Use this
            if, e.g., custom resources, loggers, or executors are desired.
        input_values (Optional[Dict[str, Any]]): A dict of input names to input values, used to
            pass inputs to the solid directly. You may also use the ``run_config`` to
            configure any inputs that are configurable.
        tags (Optional[Dict[str, Any]]): Arbitrary key-value pairs that will be added to pipeline
            logs.
        run_config (Optional[dict]): The configuration that parameterized this
            execution, as a dict.
        raise_on_error (Optional[bool]): Whether or not to raise exceptions when they occur.
            Defaults to ``True``, since this is the most useful behavior in test.

    Returns:
        Union[CompositeSolidExecutionResult, SolidExecutionResult]: The result of executing the
        solid.
    """
    check.inst_param(op_def, "op_def", NodeDefinition)
    check.opt_inst_param(mode_def, "mode_def", ModeDefinition)
    input_values = check.opt_mapping_param(input_values, "input_values", key_type=str)
    node_defs = [op_def]

    def create_value_solid(input_name, input_value):
        @lambda_solid(name=input_name)
        def input_solid():
            return input_value

        return input_solid

    dependencies: DefaultDict[str, Dict[str, IDependencyDefinition]] = defaultdict(dict)

    for input_name, input_value in input_values.items():
        dependencies[op_def.name][input_name] = DependencyDefinition(input_name)
        node_defs.append(create_value_solid(input_name, input_value))

    my_graph = GraphDefinition(
        name=f"ephemeral_{op_def.name}_solid_pipeline",
        solid_defs=node_defs,
        dependencies=dependencies,  # type: ignore
    )

    result = execute_pipeline(
        JobDefinition(
            graph_def=my_graph,
            name="ephemeral_{}_solid_pipeline".format(op_def.name),
            _mode_def=mode_def if mode_def else None,
        ),
        run_config=run_config,
        mode=mode_def.name if mode_def else None,
        tags=tags,
        raise_on_error=raise_on_error,
    )
    return result.result_for_handle(op_def.name)


@contextmanager
def copy_directory(src):
    with tempfile.TemporaryDirectory() as temp_dir:
        dst = os.path.join(temp_dir, os.path.basename(src))
        shutil.copytree(src, dst)
        yield dst


class FilesystemTestScheduler(Scheduler, ConfigurableClass):
    """This class is used in dagster core and dagster_graphql to test the scheduler's interactions
    with schedule storage, which are implemented in the methods defined on the base Scheduler class.
    Therefore, the following methods used to actually schedule jobs (e.g. create and remove cron jobs
    on a cron tab) are left unimplemented.
    """

    def __init__(self, artifacts_dir: str, inst_data: object = None):
        check.str_param(artifacts_dir, "artifacts_dir")
        self._artifacts_dir = artifacts_dir
        self._inst_data = inst_data

    @property
    def inst_data(self) -> object:
        return self._inst_data

    @classmethod
    def config_type(cls):
        return {"base_dir": str}

    @staticmethod
    def from_config_value(inst_data: object, config_value):
        return FilesystemTestScheduler(artifacts_dir=config_value["base_dir"], inst_data=inst_data)

    def debug_info(self) -> str:
        return ""

    def get_logs_path(self, _instance: DagsterInstance, schedule_origin_id: str) -> str:
        check.str_param(schedule_origin_id, "schedule_origin_id")
        return os.path.join(self._artifacts_dir, "logs", schedule_origin_id, "scheduler.log")

    def wipe(self, instance: DagsterInstance) -> None:
        pass
