import os
import pickle
import uuid
from typing import Any, Mapping, Optional

from dagster import (
    AssetMaterialization,
    AssetObservation,
    ExpectationResult,
    Failure,
    LoggerDefinition,
    ResourceDefinition,
    TypeCheck,
)
from dagster import _check as check
from dagster._core.definitions.dependency import NodeHandle
from dagster._core.definitions.events import RetryRequested
from dagster._core.definitions.op_definition import OpDefinition
from dagster._core.definitions.pipeline_base import InMemoryPipeline
from dagster._core.definitions.reconstruct import ReconstructablePipeline
from dagster._core.definitions.resource_definition import ScopedResourcesBuilder
from dagster._core.errors import DagsterInvariantViolationError
from dagster._core.events import DagsterEvent
from dagster._core.execution.api import scoped_pipeline_context
from dagster._core.execution.plan.outputs import StepOutputHandle
from dagster._core.execution.plan.plan import ExecutionPlan
from dagster._core.execution.resources_init import (
    get_required_resource_keys_to_init,
    resource_initialization_event_generator,
)
from dagster._core.instance import DagsterInstance
from dagster._core.storage.pipeline_run import DagsterRun, DagsterRunStatus
from dagster._core.system_config.objects import ResolvedRunConfig
from dagster._core.utils import make_new_run_id
from dagster._legacy import Materialization, ModeDefinition, PipelineDefinition
from dagster._loggers import colored_console_logger
from dagster._serdes import unpack_value
from dagster._utils import EventGenerationManager, ensure_gen
from dagster._utils.backcompat import canonicalize_backcompat_args, deprecation_warning

from .context import DagstermillExecutionContext, DagstermillRuntimeExecutionContext
from .errors import DagstermillError
from .serialize import PICKLE_PROTOCOL


class DagstermillResourceEventGenerationManager(EventGenerationManager):
    """Utility class to explicitly manage setup/teardown of resource events. Overrides the default
    `generate_teardown_events` method so that teardown is deferred until explicitly called by the
    dagstermill Manager
    """

    def generate_teardown_events(self):
        return iter(())

    def teardown(self):
        return [
            teardown_event
            for teardown_event in super(
                DagstermillResourceEventGenerationManager, self
            ).generate_teardown_events()
        ]


class Manager:
    def __init__(self):
        self.pipeline = None
        self.solid_def = None
        self.in_pipeline = False
        self.marshal_dir = None
        self.context = None
        self.resource_manager = None

    def _setup_resources(
        self,
        resource_defs,
        resource_configs,
        log_manager,
        execution_plan,
        pipeline_run,
        resource_keys_to_init,
        instance,
        emit_persistent_events,
    ):
        """
        Drop-in replacement for
        `dagster._core.execution.resources_init.resource_initialization_manager`.  It uses a
        `DagstermillResourceEventGenerationManager` and explicitly calls `teardown` on it
        """
        generator = resource_initialization_event_generator(
            resource_defs=resource_defs,
            resource_configs=resource_configs,
            log_manager=log_manager,
            execution_plan=execution_plan,
            pipeline_run=pipeline_run,
            resource_keys_to_init=resource_keys_to_init,
            instance=instance,
            emit_persistent_events=emit_persistent_events,
        )
        self.resource_manager = DagstermillResourceEventGenerationManager(
            generator, ScopedResourcesBuilder
        )
        return self.resource_manager

    def reconstitute_pipeline_context(
        self,
        output_log_path=None,
        marshal_dir=None,
        run_config=None,
        executable_dict=None,
        pipeline_run_dict=None,
        solid_handle_kwargs=None,
        instance_ref_dict=None,
        step_key=None,
    ):
        """Reconstitutes a context for dagstermill-managed execution.

        You'll see this function called to reconstruct a pipeline context within the ``injected
        parameters`` cell of a dagstermill output notebook. Users should not call this function
        interactively except when debugging output notebooks.

        Use :func:`dagstermill.get_context` in the ``parameters`` cell of your notebook to define a
        context for interactive exploration and development. This call will be replaced by one to
        :func:`dagstermill.reconstitute_pipeline_context` when the notebook is executed by
        dagstermill.
        """
        check.opt_str_param(output_log_path, "output_log_path")
        check.opt_str_param(marshal_dir, "marshal_dir")
        run_config = check.opt_dict_param(run_config, "run_config", key_type=str)
        check.dict_param(pipeline_run_dict, "pipeline_run_dict")
        check.dict_param(executable_dict, "executable_dict")
        check.dict_param(solid_handle_kwargs, "solid_handle_kwargs")
        check.dict_param(instance_ref_dict, "instance_ref_dict")
        check.str_param(step_key, "step_key")

        pipeline = ReconstructablePipeline.from_dict(executable_dict)
        pipeline_def = pipeline.get_definition()

        try:
            instance_ref = unpack_value(instance_ref_dict)
            instance = DagsterInstance.from_ref(instance_ref)
        except Exception as err:
            raise DagstermillError(
                "Error when attempting to resolve DagsterInstance from serialized InstanceRef"
            ) from err

        pipeline_run = unpack_value(pipeline_run_dict)

        solid_handle = NodeHandle.from_dict(solid_handle_kwargs)
        solid = pipeline_def.get_solid(solid_handle)
        solid_def = solid.definition

        self.marshal_dir = marshal_dir
        self.in_pipeline = True
        self.solid_def = solid_def
        self.pipeline = pipeline

        resolved_run_config = ResolvedRunConfig.build(
            pipeline_def, run_config, mode=pipeline_run.mode
        )

        execution_plan = ExecutionPlan.build(
            self.pipeline,
            resolved_run_config,
            step_keys_to_execute=pipeline_run.step_keys_to_execute,
        )

        with scoped_pipeline_context(
            execution_plan,
            pipeline,
            run_config,
            pipeline_run,
            instance,
            scoped_resources_builder_cm=self._setup_resources,
            # Set this flag even though we're not in test for clearer error reporting
            raise_on_error=True,
        ) as pipeline_context:
            self.context = DagstermillRuntimeExecutionContext(
                pipeline_context=pipeline_context,
                pipeline_def=pipeline_def,
                solid_config=run_config.get("ops", {}).get(solid.name, {}).get("config"),
                resource_keys_to_init=get_required_resource_keys_to_init(
                    execution_plan,
                    pipeline_def,
                    resolved_run_config,
                ),
                solid_name=solid.name,
                solid_handle=solid_handle,
                step_context=pipeline_context.for_step(execution_plan.get_step_by_key(step_key)),
            )

        return self.context

    def get_context(
        self,
        op_config: Any = None,
        resource_defs: Optional[Mapping[str, ResourceDefinition]] = None,
        logger_defs: Optional[Mapping[str, LoggerDefinition]] = None,
        solid_config: Any = None,
        mode_def: Optional[ModeDefinition] = None,
        run_config: Optional[dict] = None,
    ) -> DagstermillExecutionContext:
        """Get a dagstermill execution context for interactive exploration and development.

        Args:
            op_config (Optional[Any]): If specified, this value will be made available on the
                context as its ``op_config`` property.
            resource_defs (Optional[Mapping[str, ResourceDefinition]]): Specifies resources to provide to context.
            logger_defs (Optional[Mapping[str, LoggerDefinition]]): Specifies loggers to provide to context.
            run_config(Optional[dict]): The config dict with which to construct
                the context.

        Returns:
            :py:class:`~dagstermill.DagstermillExecutionContext`
        """
        check.opt_inst_param(mode_def, "mode_def", ModeDefinition)
        run_config = check.opt_dict_param(run_config, "run_config", key_type=str)

        if resource_defs and mode_def:
            raise DagsterInvariantViolationError(
                "Attempted to provide both resource_defs and mode_def arguments to `dagstermill.get_context`. Please provide one or the other."
            )

        if logger_defs and mode_def:
            raise DagsterInvariantViolationError(
                "Attempted to provide both logger_defs and mode_def arguments to `dagstermill.get_context`. Please provide one or the other."
            )

        solid_config = canonicalize_backcompat_args(
            op_config, "op_config", solid_config, "solid_config", "0.17.0"
        )

        if mode_def:
            deprecation_warning(
                "mode_def argument to dagstermill.get_context",
                "0.17.0",
                "Use the resource_defs argument to provide resources, and the logger_defs argument to provide loggers.",
            )

        # If we are running non-interactively, and there is already a context reconstituted, return
        # that context rather than overwriting it.
        if self.context is not None and isinstance(
            self.context, DagstermillRuntimeExecutionContext
        ):
            return self.context

        if not mode_def:
            if not logger_defs:
                logger_defs = {"dagstermill": colored_console_logger}
                run_config["loggers"] = {"dagstermill": {}}
            logger_defs = check.opt_mapping_param(logger_defs, "logger_defs")
            resource_defs = check.opt_mapping_param(resource_defs, "resource_defs")
            mode_def = ModeDefinition(logger_defs=logger_defs, resource_defs=resource_defs)

        solid_def = OpDefinition(
            name="this_solid",
            compute_fn=lambda *args, **kwargs: None,
            description="Ephemeral solid constructed by dagstermill.get_context()",
            required_resource_keys=mode_def.resource_key_set,
        )

        pipeline_def = PipelineDefinition(
            [solid_def], mode_defs=[mode_def], name="ephemeral_dagstermill_pipeline"
        )

        run_id = make_new_run_id()

        # construct stubbed PipelineRun for notebook exploration...
        # The actual pipeline run during pipeline execution will be serialized and reconstituted
        # in the `reconstitute_pipeline_context` call
        pipeline_run = DagsterRun(
            pipeline_name=pipeline_def.name,
            run_id=run_id,
            run_config=run_config,
            mode=mode_def.name,
            step_keys_to_execute=None,
            status=DagsterRunStatus.NOT_STARTED,
            tags=None,
        )

        self.in_pipeline = False
        self.solid_def = solid_def
        self.pipeline = pipeline_def

        resolved_run_config = ResolvedRunConfig.build(pipeline_def, run_config, mode=mode_def.name)

        pipeline = InMemoryPipeline(pipeline_def)
        execution_plan = ExecutionPlan.build(pipeline, resolved_run_config)

        with scoped_pipeline_context(
            execution_plan,
            pipeline,
            run_config,
            pipeline_run,
            DagsterInstance.ephemeral(),
            scoped_resources_builder_cm=self._setup_resources,
        ) as pipeline_context:

            self.context = DagstermillExecutionContext(
                pipeline_context=pipeline_context,
                pipeline_def=pipeline_def,
                solid_config=solid_config,
                resource_keys_to_init=get_required_resource_keys_to_init(
                    execution_plan,
                    pipeline_def,
                    resolved_run_config,
                ),
                solid_name=solid_def.name,
                solid_handle=NodeHandle(solid_def.name, parent=None),
            )

        return self.context

    def yield_result(self, value, output_name="result"):
        """Yield a result directly from notebook code.

        When called interactively or in development, returns its input.

        Args:
            value (Any): The value to yield.
            output_name (Optional[str]): The name of the result to yield (default: ``'result'``).
        """
        if not self.in_pipeline:
            return value

        # deferred import for perf
        import scrapbook

        if not self.solid_def.has_output(output_name):
            raise DagstermillError(
                f"Op {self.solid_def.name} does not have output named {output_name}."
                f"Expected one of {[str(output_def.name) for output_def in self.solid_def.output_defs]}"
            )

        # pass output value cross process boundary using io manager
        step_context = self.context._step_context  # pylint: disable=protected-access
        # Note: yield_result currently does not support DynamicOutput

        # dagstermill assets do not support yielding additional results within the notebook:
        if len(step_context.job_def.asset_layer.asset_keys) > 0:
            raise DagstermillError(
                "dagstermill assets do not currently support dagstermill.yield_result"
            )

        step_output_handle = StepOutputHandle(
            step_key=step_context.step.key, output_name=output_name
        )
        output_context = step_context.get_output_context(step_output_handle)
        io_manager = step_context.get_io_manager(step_output_handle)

        # Note that we assume io manager is symmetric, i.e handle_input(handle_output(X)) == X
        io_manager.handle_output(output_context, value)

        # record that the output has been yielded
        scrapbook.glue(output_name, "")

    def yield_event(self, dagster_event):
        """Yield a dagster event directly from notebook code.

        When called interactively or in development, returns its input.

        Args:
            dagster_event (Union[:class:`dagster.AssetMaterialization`, :class:`dagster.ExpectationResult`, :class:`dagster.TypeCheck`, :class:`dagster.Failure`, :class:`dagster.RetryRequested`]):
                An event to yield back to Dagster.
        """
        valid_types = (
            Materialization,
            AssetMaterialization,
            AssetObservation,
            ExpectationResult,
            TypeCheck,
            Failure,
            RetryRequested,
        )
        if not isinstance(dagster_event, valid_types):
            raise DagstermillError(
                f"Received invalid type {dagster_event} in yield_event. Expected a Dagster event type, one of {valid_types}."
            )

        if not self.in_pipeline:
            return dagster_event

        # deferred import for perf
        import scrapbook

        event_id = "event-{event_uuid}".format(event_uuid=str(uuid.uuid4()))
        out_file_path = os.path.join(self.marshal_dir, event_id)
        with open(out_file_path, "wb") as fd:
            fd.write(pickle.dumps(dagster_event, PICKLE_PROTOCOL))

        scrapbook.glue(event_id, out_file_path)

    def teardown_resources(self):
        if self.resource_manager is not None:
            self.resource_manager.teardown()

    def load_input_parameter(self, input_name: str):
        # load input from source
        step_context = self.context._step_context  # pylint: disable=protected-access
        step_input = step_context.step.step_input_named(input_name)
        input_def = step_context.solid_def.input_def_named(input_name)
        for event_or_input_value in ensure_gen(
            step_input.source.load_input_object(step_context, input_def)
        ):
            if isinstance(event_or_input_value, DagsterEvent):
                continue
            else:
                return event_or_input_value


MANAGER_FOR_NOTEBOOK_INSTANCE = Manager()
