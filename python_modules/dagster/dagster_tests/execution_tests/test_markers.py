from dagster import op, reconstructable
from dagster._core.events import MARKER_EVENTS
from dagster._core.test_utils import default_mode_def_for_test, instance_for_test
from dagster._legacy import pipeline


def define_pipeline():
    @op
    def ping():
        return "ping"

    @pipeline(mode_defs=[default_mode_def_for_test])
    def simple():
        ping()

    return simple


def test_multiproc_markers():
    with instance_for_test() as instance:
        result = reconstructable(define_pipeline).execute_in_process(
            instance=instance,
            run_config={
                "execution": {"multiprocess": {}},
            },
        )
        assert result.success
        events = instance.all_logs(result.run_id)
        start_markers = {}
        end_markers = {}
        for event in events:
            dagster_event = event.dagster_event
            if dagster_event and dagster_event.event_type in MARKER_EVENTS:
                if dagster_event.engine_event_data.marker_start:
                    key = "{step}.{marker}".format(
                        step=event.step_key,
                        marker=dagster_event.engine_event_data.marker_start,
                    )
                    start_markers[key] = event.timestamp
                if dagster_event.engine_event_data.marker_end:
                    key = "{step}.{marker}".format(
                        step=event.step_key,
                        marker=dagster_event.engine_event_data.marker_end,
                    )
                    end_markers[key] = event.timestamp

        seen = set()
        assert set(start_markers.keys()) == set(end_markers.keys())
        for key in end_markers:
            assert end_markers[key] - start_markers[key] > 0
            seen.add(key)

        assert "ping.step_process_start" in end_markers
