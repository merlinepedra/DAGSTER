"""
Repository of test pipelines
"""

import pytest

from dagster import Int, fs_io_manager, repository, resource
from dagster._check import CheckError
from dagster._legacy import ModeDefinition, JobDefinition, PresetDefinition, solid
from dagster._utils import file_relative_path


def define_empty_pipeline():
    return JobDefinition(name="empty_pipeline", solid_defs=[])


def define_single_mode_pipeline():
    @solid
    def return_two(_context):
        return 2

    return JobDefinition(
        name="single_mode",
        solid_defs=[return_two],
        mode_defs=[ModeDefinition(name="the_mode")],
    )


def define_multi_mode_pipeline():
    @solid
    def return_three(_context):
        return 3

    return JobDefinition(
        name="multi_mode",
        solid_defs=[return_three],
        mode_defs=[ModeDefinition(name="mode_one"), ModeDefinition("mode_two")],
    )


def define_multi_mode_with_resources_pipeline():
    # API red alert. One has to wrap a type in Field because it is callable
    @resource(config_schema=Int)
    def adder_resource(init_context):
        return lambda x: x + init_context.resource_config

    @resource(config_schema=Int)
    def multer_resource(init_context):
        return lambda x: x * init_context.resource_config

    @resource(config_schema={"num_one": Int, "num_two": Int})
    def double_adder_resource(init_context):
        return (
            lambda x: x
            + init_context.resource_config["num_one"]
            + init_context.resource_config["num_two"]
        )

    @solid(required_resource_keys={"op"})
    def apply_to_three(context):
        return context.resources.op(3)

    return JobDefinition(
        name="multi_mode_with_resources",
        solid_defs=[apply_to_three],
        mode_defs=[
            ModeDefinition(
                name="add_mode",
                resource_defs={"op": adder_resource, "io_manager": fs_io_manager},
            ),
            ModeDefinition(name="mult_mode", resource_defs={"op": multer_resource}),
            ModeDefinition(
                name="double_adder_mode",
                resource_defs={"op": double_adder_resource},
                description="Mode that adds two numbers to thing",
            ),
        ],
        preset_defs=[
            PresetDefinition.from_files(
                "add",
                mode="add_mode",
                config_files=[
                    file_relative_path(
                        __file__,
                        "../environments/multi_mode_with_resources/add_mode.yaml",
                    )
                ],
            ),
            PresetDefinition(
                "multiproc",
                mode="add_mode",
                run_config={
                    "resources": {"op": {"config": 2}},
                    "execution": {"multiprocess": {}},
                },
            ),
        ],
    )


@repository
def dagster_test_repository():
    return [
        define_empty_pipeline(),
        define_single_mode_pipeline(),
        define_multi_mode_pipeline(),
        define_multi_mode_with_resources_pipeline(),
    ]


def test_repository_construction():
    assert dagster_test_repository


@repository
def empty_repository():
    return []


def test_invalid_repository():
    with pytest.raises(CheckError):

        @repository
        def invalid_repository(_invalid_arg: str):
            return []
