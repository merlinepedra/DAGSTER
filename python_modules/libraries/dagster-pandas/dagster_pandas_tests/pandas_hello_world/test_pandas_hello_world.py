import os

from dagster._cli.job import do_execute_command
from dagster._core.definitions.reconstruct import ReconstructablePipeline
from dagster._core.test_utils import instance_for_test
from dagster._legacy import execute_pipeline
from dagster._utils import file_relative_path


def test_execute_pipeline():
    environment = {
        "ops": {
            "sum_op": {
                "inputs": {"num": {"csv": {"path": file_relative_path(__file__, "num.csv")}}}
            }
        }
    }

    result = execute_pipeline(
        ReconstructablePipeline.for_module("dagster_pandas.examples", "pandas_hello_world_test"),
        run_config=environment,
    )

    assert result.success

    assert result.result_for_node("sum_op").output_value().to_dict("list") == {
        "num1": [1, 3],
        "num2": [2, 4],
        "sum": [3, 7],
    }

    assert result.result_for_node("sum_sq_op").output_value().to_dict("list") == {
        "num1": [1, 3],
        "num2": [2, 4],
        "sum": [3, 7],
        "sum_sq": [9, 49],
    }


def test_cli_execute():

    # currently paths in env files have to be relative to where the
    # script has launched so we have to simulate that
    cwd = os.getcwd()
    try:

        os.chdir(file_relative_path(__file__, "../.."))
        with instance_for_test() as instance:
            do_execute_command(
                pipeline=ReconstructablePipeline.for_module(
                    "dagster_pandas.examples", "pandas_hello_world_test"
                ),
                instance=instance,
                config=[
                    file_relative_path(
                        __file__,
                        "../../dagster_pandas/examples/pandas_hello_world/*.yaml",
                    )
                ],
            )
    finally:
        # restore cwd
        os.chdir(cwd)


def test_cli_execute_failure():

    # currently paths in env files have to be relative to where the
    # script has launched so we have to simulate that
    # with pytest.raises(DagsterExecutionStepExecutionError) as e_info:
    cwd = os.getcwd()
    try:

        os.chdir(file_relative_path(__file__, "../.."))
        with instance_for_test() as instance:
            result = do_execute_command(
                pipeline=ReconstructablePipeline.for_module(
                    "dagster_pandas.examples",
                    "pandas_hello_world_fails_test",
                ),
                instance=instance,
                config=[
                    file_relative_path(
                        __file__,
                        "../../dagster_pandas/examples/pandas_hello_world/*.yaml",
                    )
                ],
            )
        failures = [event for event in result.step_event_list if event.is_failure]
    finally:
        # restore cwd
        os.chdir(cwd)

    assert len(failures) == 1
    assert "I am a programmer and I make error" in failures[0].step_failure_data.error.cause.message
