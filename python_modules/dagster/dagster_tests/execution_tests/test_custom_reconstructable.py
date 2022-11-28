import os
import sys

import pytest

from dagster import op, reconstructable
from dagster._core.definitions import (
    ReconstructablePipeline,
    build_reconstructable_pipeline,
)
from dagster._core.errors import DagsterInvariantViolationError
from dagster._legacy import pipeline


@op
def top_scope_op(_context):
    pass


class PipelineFactory:
    def __init__(self, prefix=None):
        self.prefix = prefix

    def make_pipeline(self, has_nested_scope_solid, name=None):
        @op
        def nested_scope_op(_context):
            pass

        @pipeline(name=self.prefix + name)
        def _job():
            if has_nested_scope_solid:
                nested_scope_op()
            top_scope_op()

        return _job


def reconstruct_pipeline(factory_prefix, has_nested_scope_solid, name=None):
    factory = PipelineFactory(factory_prefix)
    return factory.make_pipeline(has_nested_scope_solid, name=name)


def test_build_reconstructable_pipeline():
    sys_path = sys.path
    try:
        factory = PipelineFactory("foo_")
        bar_pipeline = factory.make_pipeline(True, name="bar")

        with pytest.raises(DagsterInvariantViolationError):
            reconstructable(bar_pipeline)

        reconstructable_bar_pipeline = build_reconstructable_pipeline(
            "test_custom_reconstructable",
            "reconstruct_job",
            ("foo_",),
            {"has_nested_scope_op": True, "name": "bar"},
            reconstructor_working_directory=os.path.dirname(os.path.realpath(__file__)),
        )

        reconstructed_bar_pipeline_def = reconstructable_bar_pipeline.get_definition()

        assert reconstructed_bar_pipeline_def.name == "foo_bar"
        assert len(reconstructed_bar_pipeline_def.solids) == 2
        assert reconstructed_bar_pipeline_def.solid_named("top_scope_op")
        assert reconstructed_bar_pipeline_def.solid_named("nested_scope_op")

    finally:
        sys.path = sys_path


def test_build_reconstructable_pipeline_serdes():
    sys_path = sys.path
    try:
        factory = PipelineFactory("foo_")
        bar_pipeline = factory.make_pipeline(True, name="bar")

        with pytest.raises(DagsterInvariantViolationError):
            reconstructable(bar_pipeline)

        sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

        reconstructable_bar_pipeline = build_reconstructable_pipeline(
            "test_custom_reconstructable",
            "reconstruct_job",
            ("foo_",),
            {"has_nested_scope_op": True, "name": "bar"},
        )

        reconstructable_bar_pipeline_dict = reconstructable_bar_pipeline.to_dict()

        reconstructed_bar_pipeline = ReconstructablePipeline.from_dict(
            reconstructable_bar_pipeline_dict
        )

        reconstructed_bar_pipeline_def = reconstructed_bar_pipeline.get_definition()

        assert reconstructed_bar_pipeline_def.name == "foo_bar"
        assert len(reconstructed_bar_pipeline_def.solids) == 2
        assert reconstructed_bar_pipeline_def.solid_named("top_scope_op")
        assert reconstructed_bar_pipeline_def.solid_named("nested_scope_op")

    finally:
        sys.path = sys_path
