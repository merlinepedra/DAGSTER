import pytest

from dagster import (
    Any,
    DagsterInvalidDefinitionError,
    DependencyDefinition,
    Int,
    List,
    MultiDependencyDefinition,
    Nothing,
)
from dagster._core.definitions.composition import MappedInputPlaceholder
from dagster._core.definitions.decorators.graph_decorator import graph
from dagster._core.definitions.graph_definition import GraphDefinition
from dagster._core.definitions.job_definition import JobDefinition
from dagster._legacy import (
    InputDefinition,
    OutputDefinition,
    execute_pipeline,
    lambda_solid,
    pipeline,
    solid,
)


def test_simple_values():
    @solid(input_defs=[InputDefinition("numbers", List[Int])])
    def sum_num(_context, numbers):
        # cant guarantee order
        assert set(numbers) == set([1, 2, 3])
        return sum(numbers)

    @lambda_solid
    def emit_1():
        return 1

    @lambda_solid
    def emit_2():
        return 2

    @lambda_solid
    def emit_3():
        return 3

    result = execute_pipeline(
        JobDefinition(
            graph_def=GraphDefinition(
                name="input_test",
                solid_defs=[emit_1, emit_2, emit_3, sum_num],
                dependencies={
                    "sum_num": {
                        "numbers": MultiDependencyDefinition(
                            [
                                DependencyDefinition("emit_1"),
                                DependencyDefinition("emit_2"),
                                DependencyDefinition("emit_3"),
                            ]
                        )
                    }
                },
            )
        )
    )
    assert result.success
    assert result.result_for_solid("sum_num").output_value() == 6


@solid(input_defs=[InputDefinition("stuff", List[Any])])
def collect(_context, stuff):
    assert set(stuff) == set([1, None, "one"])
    return stuff


@lambda_solid
def emit_num():
    return 1


@lambda_solid
def emit_none():
    pass


@lambda_solid
def emit_str():
    return "one"


@lambda_solid(output_def=OutputDefinition(Nothing))
def emit_nothing():
    pass


def test_interleaved_values():
    result = execute_pipeline(
        JobDefinition(
            name="input_test",
            solid_defs=[emit_num, emit_none, emit_str, collect],
            dependencies={
                "collect": {
                    "stuff": MultiDependencyDefinition(
                        [
                            DependencyDefinition("emit_num"),
                            DependencyDefinition("emit_none"),
                            DependencyDefinition("emit_str"),
                        ]
                    )
                }
            },
        )
    )
    assert result.success


def test_dsl():
    @pipeline
    def input_test():
        collect([emit_num(), emit_none(), emit_str()])

    result = execute_pipeline(input_test)

    assert result.success


def test_collect_one():
    @lambda_solid
    def collect_one(list_arg):
        assert list_arg == ["one"]

    @pipeline
    def multi_one():
        collect_one([emit_str()])

    assert execute_pipeline(multi_one).success


def test_fan_in_manual():
    # manually building up this guy
    @graph
    def _target_composite_dsl(str_in, none_in):
        num = emit_num()
        return collect([num, str_in, none_in])

    # base case works
    _target_graph_manual = GraphDefinition(
        name="manual_composite",
        node_defs=[emit_num, collect],
        input_mappings=[
            InputDefinition("str_in").mapping_to("collect", "stuff", 1),
            InputDefinition("none_in").mapping_to("collect", "stuff", 2),
        ],
        output_mappings=[OutputDefinition().mapping_from("collect")],
        dependencies={
            "collect": {
                "stuff": MultiDependencyDefinition(
                    [
                        DependencyDefinition("emit_num"),
                        MappedInputPlaceholder,
                        MappedInputPlaceholder,
                    ]
                )
            }
        },
    )

    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="index 2 in the MultiDependencyDefinition is not a MappedInputPlaceholder",
    ):
        _missing_placeholder = GraphDefinition(
            name="manual_graph",
            node_defs=[emit_num, collect],
            input_mappings=[
                InputDefinition("str_in").mapping_to("collect", "stuff", 1),
                InputDefinition("none_in").mapping_to("collect", "stuff", 2),
            ],
            output_mappings=[OutputDefinition().mapping_from("collect")],
            dependencies={
                "collect": {
                    "stuff": MultiDependencyDefinition(
                        [
                            DependencyDefinition("emit_num"),
                            MappedInputPlaceholder,
                        ]
                    )
                }
            },
        )

    with pytest.raises(DagsterInvalidDefinitionError, match="is not a MultiDependencyDefinition"):
        _bad_target = GraphDefinition(
            name="manual_graph",
            node_defs=[emit_num, collect],
            input_mappings=[
                InputDefinition("str_in").mapping_to("collect", "stuff", 1),
                InputDefinition("none_in").mapping_to("collect", "stuff", 2),
            ],
            output_mappings=[OutputDefinition().mapping_from("collect")],
            dependencies={"collect": {"stuff": DependencyDefinition("emit_num")}},
        )

    with pytest.raises(
        DagsterInvalidDefinitionError,
        match="Unsatisfied MappedInputPlaceholder at index 3",
    ):
        _missing_placeholder = GraphDefinition(
            name="manual_graph",
            node_defs=[emit_num, collect],
            input_mappings=[
                InputDefinition("str_in").mapping_to("collect", "stuff", 1),
                InputDefinition("none_in").mapping_to("collect", "stuff", 2),
            ],
            output_mappings=[OutputDefinition().mapping_from("collect")],
            dependencies={
                "collect": {
                    "stuff": MultiDependencyDefinition(
                        [
                            DependencyDefinition("emit_num"),
                            MappedInputPlaceholder,
                            MappedInputPlaceholder,
                            MappedInputPlaceholder,
                        ]
                    )
                }
            },
        )


def test_nothing_deps():
    JobDefinition(
        graph_def=GraphDefinition(
            name="input_test",
            solid_defs=[emit_num, emit_nothing, emit_str, collect],
            dependencies={
                "collect": {
                    "stuff": MultiDependencyDefinition(
                        [
                            DependencyDefinition("emit_num"),
                            DependencyDefinition("emit_nothing"),
                            DependencyDefinition("emit_str"),
                        ]
                    )
                }
            },
        )
    )
