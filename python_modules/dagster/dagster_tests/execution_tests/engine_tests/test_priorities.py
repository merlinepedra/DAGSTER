from dagster import op, reconstructable
from dagster._core.test_utils import default_mode_def_for_test, instance_for_test
from dagster._legacy import pipeline


@op(tags={"dagster/priority": "-1"})
def low(_):
    pass


@op
def none(_):
    pass


@op(tags={"dagster/priority": "1"})
def high(_):
    pass


@pipeline(mode_defs=[default_mode_def_for_test])
def priority_test():
    none()
    low()
    high()
    none()
    low()
    high()


def test_priorities():

    result = priority_test.execute_in_process()
    assert result.success
    assert [
        str(event.solid_handle)
        for event in result.step_event_list
        if event.is_step_success
    ] == ["high", "high_2", "none", "none_2", "low", "low_2"]


def test_priorities_mp():
    with instance_for_test() as instance:
        pipe = reconstructable(priority_test)
        result = pipe.execute_in_process(
            {
                "execution": {"multiprocess": {"config": {"max_concurrent": 1}}},
            },
            instance=instance,
        )
        assert result.success
        assert [
            str(event.solid_handle)
            for event in result.step_event_list
            if event.is_step_success
        ] == ["high", "high_2", "none", "none_2", "low", "low_2"]
