from dagster import job, In, op, List, Optional


def test_from_intermediates_from_multiple_outputs():
    @op
    def x():
        return "x"

    @op
    def y():
        return "y"

    @op(ins={"stuff": In(Optional[List[str]])})
    def gather(stuff):
        return "{} and {}".format(*stuff)

    @job
    def pipe():
        gather([x(), y()])

    result = pipe.execute_in_process()

    assert result
    assert result.success
    assert (
        result.result_for_solid("gather")
        .compute_input_event_dict["stuff"]
        .event_specific_data[1]
        .label
        == "stuff"
    )
    assert result.result_for_solid("gather").output_value() == "x and y"


def test_from_intermediates_from_config():
    run_config = {
        "solids": {"x": {"inputs": {"string_input": {"value": "Dagster is great!"}}}}
    }

    @op
    def x(string_input):
        return string_input

    @job
    def pipe():
        x()

    result = pipe.execute_in_process(run_config=run_config)

    assert result
    assert result.success
    assert (
        result.result_for_solid("x")
        .compute_input_event_dict["string_input"]
        .event_specific_data[1]
        .label
        == "string_input"
    )
    assert result.result_for_solid("x").output_value() == "Dagster is great!"
