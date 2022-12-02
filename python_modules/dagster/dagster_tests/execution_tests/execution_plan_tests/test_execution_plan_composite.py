from dagster import Field, Int, String
from dagster._core.definitions.config import ConfigMapping
from dagster._core.definitions.decorators.graph_decorator import graph
from dagster._core.definitions.pipeline_base import InMemoryPipeline
from dagster._core.execution.api import create_execution_plan, execute_plan
from dagster._core.instance import DagsterInstance
from dagster._legacy import pipeline, solid


@solid(config_schema={"foo": Field(String)})
def node_a(context):
    return context.solid_config["foo"]


@solid(config_schema={"bar": Int})
def node_b(context, input_):
    return input_ * context.solid_config["bar"]


@graph
def composite_with_nested_config_solid():
    return node_b(node_a())


@pipeline
def composite_pipeline():
    composite_with_nested_config_solid()


@graph(
    config=ConfigMapping(
        config_fn=lambda cfg: {
            "node_a": {"config": {"foo": cfg["foo"]}},
            "node_b": {"config": {"bar": cfg["bar"]}},
        },
        config_schema={"foo": Field(String), "bar": Int},
    )
)
def composite_with_nested_config_solid_and_config_mapping():
    return node_b(node_a())


@pipeline
def composite_pipeline_with_config_mapping():
    composite_with_nested_config_solid_and_config_mapping()


def test_execution_plan_for_graph():
    run_config = {
        "solids": {
            "composite_with_nested_config_solid": {
                "solids": {
                    "node_a": {"config": {"foo": "baz"}},
                    "node_b": {"config": {"bar": 3}},
                }
            }
        }
    }
    execution_plan = create_execution_plan(composite_pipeline, run_config=run_config)
    instance = DagsterInstance.ephemeral()
    pipeline_run = instance.create_run_for_pipeline(
        pipeline_def=composite_pipeline, execution_plan=execution_plan
    )
    events = execute_plan(
        execution_plan,
        InMemoryPipeline(composite_pipeline),
        run_config=run_config,
        pipeline_run=pipeline_run,
        instance=instance,
    )

    assert [e.event_type_value for e in events] == [
        "RESOURCE_INIT_STARTED",
        "RESOURCE_INIT_SUCCESS",
        "LOGS_CAPTURED",
        "STEP_START",
        "STEP_OUTPUT",
        "HANDLED_OUTPUT",
        "STEP_SUCCESS",
        "STEP_START",
        "LOADED_INPUT",
        "STEP_INPUT",
        "STEP_OUTPUT",
        "HANDLED_OUTPUT",
        "STEP_SUCCESS",
    ]


def test_execution_plan_for_graph_with_config_mapping():
    run_config = {
        "solids": {
            "composite_with_nested_config_solid_and_config_mapping": {
                "config": {"foo": "baz", "bar": 3}
            }
        }
    }
    execution_plan = create_execution_plan(
        composite_pipeline_with_config_mapping, run_config=run_config
    )
    instance = DagsterInstance.ephemeral()
    pipeline_run = instance.create_run_for_pipeline(
        pipeline_def=composite_pipeline_with_config_mapping,
        execution_plan=execution_plan,
    )

    events = execute_plan(
        execution_plan,
        InMemoryPipeline(composite_pipeline_with_config_mapping),
        run_config=run_config,
        pipeline_run=pipeline_run,
        instance=instance,
    )

    assert [e.event_type_value for e in events] == [
        "RESOURCE_INIT_STARTED",
        "RESOURCE_INIT_SUCCESS",
        "LOGS_CAPTURED",
        "STEP_START",
        "STEP_OUTPUT",
        "HANDLED_OUTPUT",
        "STEP_SUCCESS",
        "STEP_START",
        "LOADED_INPUT",
        "STEP_INPUT",
        "STEP_OUTPUT",
        "HANDLED_OUTPUT",
        "STEP_SUCCESS",
    ]
