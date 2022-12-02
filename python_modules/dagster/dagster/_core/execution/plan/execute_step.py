import inspect
import warnings
from collections import defaultdict
from typing import (
    AbstractSet,
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)

import dagster._check as check
from dagster._core.definitions import (
    AssetKey,
    AssetMaterialization,
    AssetObservation,
    ExpectationResult,
    Materialization,
    Output,
    OutputDefinition,
    TypeCheck,
)
from dagster._core.definitions.decorators.solid_decorator import DecoratedOpFunction
from dagster._core.definitions.events import AssetLineageInfo, DynamicOutput
from dagster._core.definitions.logical_version import (
    CODE_VERSION_TAG_KEY,
    DEFAULT_LOGICAL_VERSION,
    LOGICAL_VERSION_TAG_KEY,
    LogicalVersion,
    compute_logical_version,
    extract_logical_version_from_entry,
    get_input_event_pointer_tag_key,
    get_input_logical_version_tag_key,
)
from dagster._core.definitions.metadata import (
    MetadataEntry,
    PartitionMetadataEntry,
    normalize_metadata,
)
from dagster._core.definitions.multi_dimensional_partitions import (
    MultiPartitionKey,
    get_tags_from_multi_partition_key,
)
from dagster._core.errors import (
    DagsterExecutionHandleOutputError,
    DagsterInvariantViolationError,
    DagsterStepOutputNotFoundError,
    DagsterTypeCheckDidNotPass,
    DagsterTypeCheckError,
    DagsterTypeMaterializationError,
    user_code_error_boundary,
)
from dagster._core.events import DagsterEvent
from dagster._core.execution.context.output import OutputContext
from dagster._core.execution.context.system import StepExecutionContext, TypeCheckContext
from dagster._core.execution.plan.compute import execute_core_compute
from dagster._core.execution.plan.inputs import StepInputData
from dagster._core.execution.plan.objects import StepSuccessData, TypeCheckData
from dagster._core.execution.plan.outputs import StepOutputData, StepOutputHandle
from dagster._core.execution.resolve_versions import resolve_step_output_versions
from dagster._core.storage.tags import MEMOIZED_RUN_TAG
from dagster._core.types.dagster_type import DagsterType
from dagster._utils import ensure_gen, iterate_with_context
from dagster._utils.backcompat import ExperimentalWarning, experimental_functionality_warning
from dagster._utils.timing import time_execution_scope

from .compute import SolidOutputUnion
from .compute_generator import create_solid_compute_wrapper
from .utils import op_execution_error_boundary


def _step_output_error_checked_user_event_sequence(
    step_context: StepExecutionContext, user_event_sequence: Iterator[SolidOutputUnion]
) -> Iterator[SolidOutputUnion]:
    """
    Process the event sequence to check for invariant violations in the event
    sequence related to Output events emitted from the compute_fn.

    This consumes and emits an event sequence.
    """
    check.inst_param(step_context, "step_context", StepExecutionContext)
    check.iterator_param(user_event_sequence, "user_event_sequence")

    step = step_context.step
    op_label = step_context.describe_op()
    output_names = list([output_def.name for output_def in step.step_outputs])

    for user_event in user_event_sequence:
        if not isinstance(user_event, (Output, DynamicOutput)):
            yield user_event
            continue

        # do additional processing on Outputs
        output = user_event
        if not step.has_step_output(cast(str, output.output_name)):
            raise DagsterInvariantViolationError(
                f'Core compute for {op_label} returned an output "{output.output_name}" that does '
                f"not exist. The available outputs are {output_names}"
            )

        step_output = step.step_output_named(cast(str, output.output_name))
        output_def = step_context.pipeline_def.get_solid(step_output.solid_handle).output_def_named(
            step_output.name
        )

        if isinstance(output, Output):
            if step_context.has_seen_output(output.output_name):
                raise DagsterInvariantViolationError(
                    f'Compute for {op_label} returned an output "{output.output_name}" multiple '
                    "times"
                )

            if output_def.is_dynamic:
                raise DagsterInvariantViolationError(
                    f'Compute for {op_label} for output "{output.output_name}" defined as dynamic '
                    "must yield DynamicOutput, got Output."
                )

            step_context.observe_output(output.output_name)

            metadata = step_context.get_output_metadata(output.output_name)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=DeprecationWarning)

                output = Output(
                    value=output.value,
                    output_name=output.output_name,
                    metadata_entries=[
                        *output.metadata_entries,
                        *normalize_metadata(cast(Dict[str, Any], metadata), []),
                    ],
                )
        else:
            if not output_def.is_dynamic:
                raise DagsterInvariantViolationError(
                    f"Compute for {op_label} yielded a DynamicOutput, but did not use "
                    "DynamicOutputDefinition."
                )
            if step_context.has_seen_output(output.output_name, output.mapping_key):
                raise DagsterInvariantViolationError(
                    f"Compute for {op_label} yielded a DynamicOutput with mapping_key "
                    f'"{output.mapping_key}" multiple times.'
                )
            step_context.observe_output(output.output_name, output.mapping_key)
            metadata = step_context.get_output_metadata(
                output.output_name, mapping_key=output.mapping_key
            )
            output = DynamicOutput(
                value=output.value,
                output_name=output.output_name,
                metadata_entries=[
                    *output.metadata_entries,
                    *normalize_metadata(cast(Dict[str, Any], metadata), []),
                ],
                mapping_key=output.mapping_key,
            )

        yield output

    for step_output in step.step_outputs:
        step_output_def = step_context.solid_def.output_def_named(step_output.name)
        if not step_context.has_seen_output(step_output_def.name) and not step_output_def.optional:
            if step_output_def.dagster_type.is_nothing:
                step_context.log.info(
                    f'Emitting implicit Nothing for output "{step_output_def.name}" on {op_label}'
                )
                yield Output(output_name=step_output_def.name, value=None)
            elif not step_output_def.is_dynamic:
                raise DagsterStepOutputNotFoundError(
                    (
                        f"Core compute for {op_label} did not return an output for non-optional "
                        f'output "{step_output_def.name}"'
                    ),
                    step_key=step.key,
                    output_name=step_output_def.name,
                )


def do_type_check(context: TypeCheckContext, dagster_type: DagsterType, value: Any) -> TypeCheck:
    type_check = dagster_type.type_check(context, value)
    if not isinstance(type_check, TypeCheck):
        return TypeCheck(
            success=False,
            description=(
                "Type checks must return TypeCheck. Type check for type {type_name} returned "
                "value of type {return_type} when checking runtime value of type {dagster_type}."
            ).format(
                type_name=dagster_type.display_name,
                return_type=type(type_check),
                dagster_type=type(value),
            ),
        )
    return type_check


def _create_step_input_event(
    step_context: StepExecutionContext, input_name: str, type_check: TypeCheck, success: bool
) -> DagsterEvent:
    return DagsterEvent.step_input_event(
        step_context,
        StepInputData(
            input_name=input_name,
            type_check_data=TypeCheckData(
                success=success,
                label=input_name,
                description=type_check.description if type_check else None,
                metadata_entries=type_check.metadata_entries if type_check else [],
            ),
        ),
    )


def _type_checked_event_sequence_for_input(
    step_context: StepExecutionContext,
    input_name: str,
    input_value: Any,
) -> Iterator[DagsterEvent]:
    check.inst_param(step_context, "step_context", StepExecutionContext)
    check.str_param(input_name, "input_name")

    step_input = step_context.step.step_input_named(input_name)
    input_def = step_context.solid_def.input_def_named(step_input.name)

    check.invariant(
        input_def.name == input_name,
        f"InputDefinition name does not match, expected {input_name} got {input_def.name}",
    )

    dagster_type = input_def.dagster_type
    type_check_context = step_context.for_type(dagster_type)
    input_type = type(input_value)
    op_label = step_context.describe_op()

    with user_code_error_boundary(
        DagsterTypeCheckError,
        lambda: (
            f'Error occurred while type-checking input "{input_name}" of {op_label}, with Python '
            f"type {input_type} and Dagster type {dagster_type.display_name}"
        ),
        log_manager=type_check_context.log,
    ):
        type_check = do_type_check(type_check_context, dagster_type, input_value)

    yield _create_step_input_event(
        step_context, input_name, type_check=type_check, success=type_check.success
    )

    if not type_check.success:
        raise DagsterTypeCheckDidNotPass(
            description=(
                f'Type check failed for step input "{input_name}" - '
                f'expected type "{dagster_type.display_name}". '
                f"Description: {type_check.description}"
            ),
            metadata_entries=type_check.metadata_entries,
            dagster_type=dagster_type,
        )


def _type_check_output(
    step_context: StepExecutionContext,
    step_output_handle: StepOutputHandle,
    output: Any,
    version: Optional[str],
) -> Iterator[DagsterEvent]:
    check.inst_param(step_context, "step_context", StepExecutionContext)
    check.inst_param(output, "output", (Output, DynamicOutput))

    step_output = step_context.step.step_output_named(output.output_name)
    step_output_def = step_context.solid_def.output_def_named(step_output.name)

    dagster_type = step_output_def.dagster_type
    type_check_context = step_context.for_type(dagster_type)
    op_label = step_context.describe_op()
    output_type = type(output.value)

    with user_code_error_boundary(
        DagsterTypeCheckError,
        lambda: (
            f'Error occurred while type-checking output "{output.output_name}" of {op_label}, with '
            f"Python type {output_type} and Dagster type {dagster_type.display_name}"
        ),
        log_manager=type_check_context.log,
    ):
        type_check = do_type_check(type_check_context, dagster_type, output.value)

    yield DagsterEvent.step_output_event(
        step_context=step_context,
        step_output_data=StepOutputData(
            step_output_handle=step_output_handle,
            type_check_data=TypeCheckData(
                success=type_check.success,
                label=step_output_handle.output_name,
                description=type_check.description if type_check else None,
                metadata_entries=type_check.metadata_entries if type_check else [],
            ),
            version=version,
            metadata_entries=[
                entry for entry in output.metadata_entries if isinstance(entry, MetadataEntry)
            ],
        ),
    )

    if not type_check.success:
        raise DagsterTypeCheckDidNotPass(
            description=(
                f'Type check failed for step output "{output.output_name}" - '
                f'expected type "{dagster_type.display_name}". '
                f"Description: {type_check.description}"
            ),
            metadata_entries=type_check.metadata_entries,
            dagster_type=dagster_type,
        )


def core_dagster_event_sequence_for_step(
    step_context: StepExecutionContext,
) -> Iterator[DagsterEvent]:
    """
    Execute the step within the step_context argument given the in-memory
    events. This function yields a sequence of DagsterEvents, but without
    catching any exceptions that have bubbled up during the computation
    of the step.
    """
    check.inst_param(step_context, "step_context", StepExecutionContext)

    if step_context.previous_attempt_count > 0:
        yield DagsterEvent.step_restarted_event(step_context, step_context.previous_attempt_count)
    else:
        yield DagsterEvent.step_start_event(step_context)

    inputs = {}

    if step_context.step_materializes_assets:
        step_context.fetch_external_input_asset_records()

    for step_input in step_context.step.step_inputs:
        input_def = step_context.solid_def.input_def_named(step_input.name)
        dagster_type = input_def.dagster_type

        if dagster_type.is_nothing:
            continue

        for event_or_input_value in ensure_gen(
            step_input.source.load_input_object(step_context, input_def)
        ):
            if isinstance(event_or_input_value, DagsterEvent):
                yield event_or_input_value
            else:
                check.invariant(step_input.name not in inputs)
                inputs[step_input.name] = event_or_input_value

    for input_name, input_value in inputs.items():
        for evt in check.generator(
            _type_checked_event_sequence_for_input(step_context, input_name, input_value)
        ):
            yield evt

    input_lineage = step_context.get_input_lineage()

    # The core execution loop expects a compute generator in a specific format: a generator that
    # takes a context and dictionary of inputs as input, yields output events. If a solid definition
    # was generated from the @solid or @lambda_solid decorator, then compute_fn needs to be coerced
    # into this format. If the solid definition was created directly, then it is expected that the
    # compute_fn is already in this format.
    if isinstance(step_context.solid_def.compute_fn, DecoratedOpFunction):
        core_gen = create_solid_compute_wrapper(step_context.solid_def)
    else:
        core_gen = step_context.solid_def.compute_fn

    with time_execution_scope() as timer_result:
        user_event_sequence = check.generator(
            execute_core_compute(
                step_context,
                inputs,
                core_gen,
            )
        )

        # It is important for this loop to be indented within the
        # timer block above in order for time to be recorded accurately.
        for user_event in check.generator(
            _step_output_error_checked_user_event_sequence(step_context, user_event_sequence)
        ):
            if isinstance(user_event, DagsterEvent):
                yield user_event
            elif isinstance(user_event, (Output, DynamicOutput)):
                for evt in _type_check_and_store_output(step_context, user_event, input_lineage):
                    yield evt
            # for now, I'm ignoring AssetMaterializations yielded manually, but we might want
            # to do something with these in the above path eventually
            elif isinstance(user_event, (AssetMaterialization, Materialization)):
                yield DagsterEvent.asset_materialization(step_context, user_event, input_lineage)
            elif isinstance(user_event, AssetObservation):
                yield DagsterEvent.asset_observation(step_context, user_event)
            elif isinstance(user_event, ExpectationResult):
                yield DagsterEvent.step_expectation_result(step_context, user_event)
            else:
                check.failed(
                    "Unexpected event {event}, should have been caught earlier".format(
                        event=user_event
                    )
                )

    yield DagsterEvent.step_success_event(
        step_context, StepSuccessData(duration_ms=timer_result.millis)
    )


def _type_check_and_store_output(
    step_context: StepExecutionContext,
    output: Union[DynamicOutput, Output],
    input_lineage: Sequence[AssetLineageInfo],
) -> Iterator[DagsterEvent]:

    check.inst_param(step_context, "step_context", StepExecutionContext)
    check.inst_param(output, "output", (Output, DynamicOutput))
    check.sequence_param(input_lineage, "input_lineage", AssetLineageInfo)

    mapping_key = output.mapping_key if isinstance(output, DynamicOutput) else None

    step_output_handle = StepOutputHandle(
        step_key=step_context.step.key, output_name=output.output_name, mapping_key=mapping_key
    )

    # If we are executing using the execute_in_process API, then we allow for the outputs of solids
    # to be directly captured to a dictionary after they are computed.
    if step_context.output_capture is not None:
        step_context.output_capture[step_output_handle] = output.value
    # capture output at the step level for threading the computed output values to hook context
    if step_context.step_output_capture is not None:
        step_context.step_output_capture[step_output_handle] = output.value

    version = (
        resolve_step_output_versions(
            step_context.pipeline_def, step_context.execution_plan, step_context.resolved_run_config
        ).get(step_output_handle)
        if MEMOIZED_RUN_TAG in step_context.pipeline.get_definition().tags
        else None
    )

    for output_event in _type_check_output(step_context, step_output_handle, output, version):
        yield output_event

    for evt in _store_output(step_context, step_output_handle, output, input_lineage):
        yield evt

    for evt in _create_type_materializations(step_context, output.output_name, output.value):
        yield evt


def _asset_key_and_partitions_for_output(
    output_context: OutputContext,
) -> Tuple[Optional[AssetKey], AbstractSet[str]]:

    output_asset_info = output_context.asset_info

    if output_asset_info:
        if not output_asset_info.is_required:
            output_context.log.warn(f"Materializing unexpected asset key: {output_asset_info.key}.")
        return (
            output_asset_info.key,
            output_asset_info.partitions_fn(output_context) or set(),
        )

    return None, set()


def _dedup_asset_lineage(asset_lineage: Sequence[AssetLineageInfo]) -> Sequence[AssetLineageInfo]:
    """Method to remove duplicate specifications of the same Asset/Partition pair from the lineage
    information. Duplicates can occur naturally when calculating transitive dependencies from solids
    with multiple Outputs, which in turn have multiple Inputs (because each Output of the solid will
    inherit all dependencies from all of the solid Inputs).
    """
    key_partition_mapping: Dict[AssetKey, Set[str]] = defaultdict(set)

    for lineage_info in asset_lineage:
        if not lineage_info.partitions:
            key_partition_mapping[lineage_info.asset_key] |= set()
        for partition in lineage_info.partitions:
            key_partition_mapping[lineage_info.asset_key].add(partition)
    return [
        AssetLineageInfo(asset_key=asset_key, partitions=partitions)
        for asset_key, partitions in key_partition_mapping.items()
    ]


def _get_output_asset_materializations(
    asset_key: AssetKey,
    asset_partitions: AbstractSet[str],
    output: Union[Output, DynamicOutput],
    output_def: OutputDefinition,
    io_manager_metadata_entries: Sequence[Union[MetadataEntry, PartitionMetadataEntry]],
    step_context: StepExecutionContext,
) -> Iterator[AssetMaterialization]:

    all_metadata = [*output.metadata_entries, *io_manager_metadata_entries]

    # Clear any cached record associated with this asset, since we are about to generate a new
    # materialization.
    step_context.wipe_input_asset_record(asset_key)

    tags: Dict[str, str]
    if (
        step_context.is_external_input_asset_records_loaded
        and asset_key in step_context.pipeline_def.asset_layer.asset_keys
    ):
        tags = _build_logical_version_tags(asset_key, step_context)
        logical_version = LogicalVersion(tags[LOGICAL_VERSION_TAG_KEY])
        step_context.record_logical_version(asset_key, logical_version)
    else:
        tags = {}

    if asset_partitions:
        metadata_mapping: Dict[
            str,
            List[Union[MetadataEntry, PartitionMetadataEntry]],
        ] = {partition: [] for partition in asset_partitions}

        for entry in all_metadata:
            # TODO: Allow users to specify a multi-dimensional partition key in a PartitionMetadataEntry

            # if you target a given entry at a partition, only apply it to the requested partition
            # otherwise, apply it to all partitions
            if isinstance(entry, PartitionMetadataEntry):
                if entry.partition not in asset_partitions:
                    raise DagsterInvariantViolationError(
                        f"Output {output_def.name} associated a metadata entry ({entry}) with the partition "
                        f"`{entry.partition}`, which is not one of the declared partition mappings ({asset_partitions})."
                    )
                metadata_mapping[entry.partition].append(entry.entry)
            else:
                for partition in metadata_mapping.keys():
                    metadata_mapping[partition].append(entry)

        for partition in asset_partitions:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=DeprecationWarning)

                tags.update(
                    get_tags_from_multi_partition_key(partition)
                    if isinstance(partition, MultiPartitionKey)
                    else {}
                )

                yield AssetMaterialization(
                    asset_key=asset_key,
                    partition=partition,
                    metadata_entries=metadata_mapping[partition],
                    tags=tags,
                )
    else:
        for entry in all_metadata:
            if isinstance(entry, PartitionMetadataEntry):
                raise DagsterInvariantViolationError(
                    f"Output {output_def.name} got a PartitionMetadataEntry ({entry}), but "
                    "is not associated with any specific partitions."
                )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=DeprecationWarning)

            yield AssetMaterialization(
                asset_key=asset_key, metadata_entries=all_metadata, tags=tags
            )


def _build_logical_version_tags(
    asset_key: AssetKey, step_context: StepExecutionContext
) -> Dict[str, str]:
    asset_layer = step_context.pipeline_def.asset_layer
    code_version = asset_layer.code_version_for_asset(asset_key) or step_context.pipeline_run.run_id
    input_logical_versions: Dict[AssetKey, LogicalVersion] = {}
    tags: Dict[str, str] = {}
    tags[CODE_VERSION_TAG_KEY] = code_version
    deps = step_context.pipeline_def.asset_layer.upstream_assets_for_asset(asset_key)
    for key in deps:
        # For deps external to this step, this will retrieve the cached record that was stored prior
        # to step execution. For inputs internal to this step, it may trigger a query to retrieve
        # the most recent materialization record (it will retrieve a cached record if it's already
        # been asked for). For this to be correct, the output materializations for the step must be
        # generated in topological order -- we assume this.
        event = step_context.get_input_asset_record(key)
        if event is not None:
            logical_version = (
                extract_logical_version_from_entry(event.event_log_entry) or DEFAULT_LOGICAL_VERSION
            )
        else:
            logical_version = DEFAULT_LOGICAL_VERSION
        input_logical_versions[key] = logical_version
        tags[get_input_logical_version_tag_key(key)] = logical_version.value
        tags[get_input_event_pointer_tag_key(key)] = str(event.storage_id) if event else "NULL"

    logical_version = compute_logical_version(code_version, input_logical_versions)
    tags[LOGICAL_VERSION_TAG_KEY] = logical_version.value
    return tags


def _store_output(
    step_context: StepExecutionContext,
    step_output_handle: StepOutputHandle,
    output: Union[Output, DynamicOutput],
    input_lineage: Sequence[AssetLineageInfo],
) -> Iterator[DagsterEvent]:

    output_def = step_context.solid_def.output_def_named(step_output_handle.output_name)
    output_manager = step_context.get_io_manager(step_output_handle)
    output_context = step_context.get_output_context(step_output_handle)

    manager_materializations = []
    manager_metadata_entries: List[Union[PartitionMetadataEntry, MetadataEntry]] = []

    # output_manager.handle_output is either a generator function, or a normal function with or
    # without a return value. In the case that handle_output is a normal function, we need to
    # catch errors should they be raised before a return value. We can do this by wrapping
    # handle_output in a generator so that errors will be caught within iterate_with_context.

    if not inspect.isgeneratorfunction(output_manager.handle_output):

        def _gen_fn():
            gen_output = output_manager.handle_output(output_context, output.value)
            for event in output_context.consume_events():
                yield event
            if gen_output:
                yield gen_output

        handle_output_gen = _gen_fn()
    else:
        handle_output_gen = output_manager.handle_output(output_context, output.value)

    for elt in iterate_with_context(
        lambda: op_execution_error_boundary(
            DagsterExecutionHandleOutputError,
            msg_fn=lambda: (
                f'Error occurred while handling output "{output_context.name}" of '
                f'step "{step_context.step.key}":'
            ),
            step_context=step_context,
            step_key=step_context.step.key,
            output_name=output_context.name,
        ),
        handle_output_gen,
    ):
        for event in output_context.consume_events():
            yield event

        manager_metadata_entries.extend(output_context.consume_logged_metadata_entries())
        if isinstance(elt, DagsterEvent):
            yield elt
        elif isinstance(elt, AssetMaterialization):
            manager_materializations.append(elt)
        elif isinstance(elt, (MetadataEntry, PartitionMetadataEntry)):
            experimental_functionality_warning(
                "Yielding metadata from an IOManager's handle_output() function"
            )
            manager_metadata_entries.append(elt)
        else:
            raise DagsterInvariantViolationError(
                f"IO manager on output {output_def.name} has returned "
                f"value {elt} of type {type(elt).__name__}. The return type can only be "
                "one of AssetMaterialization, MetadataEntry, PartitionMetadataEntry."
            )

    for event in output_context.consume_events():
        yield event

    manager_metadata_entries.extend(output_context.consume_logged_metadata_entries())
    # do not alter explicitly created AssetMaterializations
    for materialization in manager_materializations:
        if materialization.metadata_entries and manager_metadata_entries:
            raise DagsterInvariantViolationError(
                f"When handling output '{output_context.name}' of {output_context.op_def.node_type_str} '{output_context.op_def.name}', received a materialization with metadata, while context.add_output_metadata was used within the same call to handle_output. Due to potential conflicts, this is not allowed. Please specify metadata in one place within the `handle_output` function."
            )

        if manager_metadata_entries:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=ExperimentalWarning)

                materialization = AssetMaterialization(
                    asset_key=materialization.asset_key,
                    description=materialization.description,
                    metadata_entries=manager_metadata_entries,
                    partition=materialization.partition,
                    metadata=None,
                )
        yield DagsterEvent.asset_materialization(step_context, materialization, input_lineage)

    asset_key, partitions = _asset_key_and_partitions_for_output(output_context)
    if asset_key:
        for materialization in _get_output_asset_materializations(
            asset_key,
            partitions,
            output,
            output_def,
            manager_metadata_entries,
            step_context,
        ):
            yield DagsterEvent.asset_materialization(
                step_context,
                materialization,
                input_lineage,
            )

    yield DagsterEvent.handled_output(
        step_context,
        output_name=step_output_handle.output_name,
        manager_key=output_def.io_manager_key,
        metadata_entries=[
            entry for entry in manager_metadata_entries if isinstance(entry, MetadataEntry)
        ],
    )


def _create_type_materializations(
    step_context: StepExecutionContext, output_name: str, value: Any
) -> Iterator[DagsterEvent]:
    """If the output has any dagster type materializers, runs them."""

    step = step_context.step
    current_handle = step.solid_handle

    # check for output mappings at every point up the composition hierarchy
    while current_handle:
        solid_config = step_context.resolved_run_config.solids.get(current_handle.to_string())
        current_handle = current_handle.parent

        if solid_config is None:
            continue

        for output_spec in solid_config.outputs.type_materializer_specs:
            check.invariant(len(output_spec) == 1)  # type: ignore
            config_output_name, output_spec = list(output_spec.items())[0]  # type: ignore
            if config_output_name == output_name:
                step_output = step.step_output_named(output_name)
                with user_code_error_boundary(
                    DagsterTypeMaterializationError,
                    msg_fn=lambda: (
                        "Error occurred during output materialization:"
                        f'\n    output name: "{output_name}"'
                        f'\n    solid invocation: "{step_context.solid.name}"'
                        f'\n    solid definition: "{step_context.solid_def.name}"'
                    ),
                    log_manager=step_context.log,
                ):
                    output_def = step_context.solid_def.output_def_named(step_output.name)
                    dagster_type = output_def.dagster_type
                    materializer = dagster_type.materializer
                    if materializer is None:
                        check.failed(
                            "Unexpected attempt to materialize with no materializer available on dagster_type"
                        )
                    materializations = materializer.materialize_runtime_values(
                        step_context.get_type_materializer_context(), output_spec, value
                    )

                for materialization in materializations:
                    if not isinstance(materialization, (AssetMaterialization, Materialization)):
                        raise DagsterInvariantViolationError(
                            (
                                "materialize_runtime_values on type {type_name} has returned "
                                "value {value} of type {python_type}. You must return an "
                                "AssetMaterialization."
                            ).format(
                                type_name=dagster_type.display_name,
                                value=repr(materialization),
                                python_type=type(materialization).__name__,
                            )
                        )

                    yield DagsterEvent.asset_materialization(step_context, materialization)
