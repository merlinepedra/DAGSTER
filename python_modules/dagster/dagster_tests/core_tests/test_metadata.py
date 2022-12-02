from dagster import NodeInvocation
from dagster._core.definitions.graph_definition import GraphDefinition
from dagster._core.definitions.job_definition import JobDefinition
from dagster._legacy import execute_pipeline, solid


def test_solid_instance_tags():
    called = {}

    @solid(tags={"foo": "bar", "baz": "quux"})
    def metadata_solid(context):
        assert context.solid.tags == {"foo": "oof", "baz": "quux", "bip": "bop"}
        called["yup"] = True

    pipeline = JobDefinition(
        graph_def=GraphDefinition(
            name="metadata_pipeline",
            node_defs=[metadata_solid],
            dependencies={
                NodeInvocation(
                    "metadata_solid",
                    alias="aliased_metadata_solid",
                    tags={"foo": "oof", "bip": "bop"},
                ): {}
            },
        ),
    )

    result = execute_pipeline(
        pipeline,
    )

    assert result.success
    assert called["yup"]
