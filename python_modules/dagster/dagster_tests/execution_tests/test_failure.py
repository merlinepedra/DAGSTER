from dagster import Failure, MetadataEntry
from dagster._legacy import execute_pipeline, lambda_solid, pipeline


def test_failure():
    @lambda_solid
    def throw():
        raise Failure(
            description="it Failure",
            metadata_entries=[MetadataEntry("label", value="text")],
        )

    @pipeline
    def failure():
        throw()

    result = execute_pipeline(failure, raise_on_error=False)
    assert not result.success
    failure_data = result.result_for_node("throw").failure_data
    assert failure_data
    assert failure_data.error.cls_name == "Failure"

    # hard coded
    assert failure_data.user_failure_data.label == "intentional-failure"
    # from Failure
    assert failure_data.user_failure_data.description == "it Failure"
    assert failure_data.user_failure_data.metadata_entries[0].label == "label"
    assert failure_data.user_failure_data.metadata_entries[0].value.text == "text"
