import os

import pytest
from dagstermill.examples.repository import hello_world

from dagster import job
from dagster._core.errors import DagsterInvalidDefinitionError

from .test_ops import exec_for_test


def test_yes_output_notebook_no_file_manager():
    with pytest.raises(DagsterInvalidDefinitionError):

        @job
        def _job():
            hello_world()


@pytest.mark.notebook_test
def test_no_output_notebook_yes_file_manager():
    # when output_notebook is not set and file_manager is provided:
    # * persist output notebook (but no op output)
    with exec_for_test("hello_world_no_output_notebook_job") as result:
        assert result.success
        materializations = [
            x for x in result.event_list if x.event_type_value == "ASSET_MATERIALIZATION"
        ]
        assert len(materializations) == 0

        assert result.result_for_node("hello_world_no_output_notebook").success
        assert not result.result_for_node("hello_world_no_output_notebook").output_values


@pytest.mark.notebook_test
def test_no_output_notebook_no_file_manager():
    # when output_notebook is not set and file_manager is not provided:
    # * throw warning and persisting fails
    with exec_for_test("hello_world_no_output_notebook_no_file_manager_job") as result:
        assert result.success
        materializations = [
            x for x in result.event_list if x.event_type_value == "ASSET_MATERIALIZATION"
        ]
        assert len(materializations) == 0

        assert result.result_for_node("hello_world_no_output_notebook").success
        assert not result.result_for_node("hello_world_no_output_notebook").output_values


@pytest.mark.notebook_test
def test_yes_output_notebook_yes_io_manager():
    # when output_notebook is set and io manager is provided:
    # * persist the output notebook via notebook_io_manager
    # * yield AssetMaterialization
    # * yield Output(value=bytes, name=output_notebook)
    with exec_for_test("hello_world_with_output_notebook_job") as result:
        assert result.success
        materializations = [
            x for x in result.event_list if x.event_type_value == "ASSET_MATERIALIZATION"
        ]
        assert len(materializations) == 1

        assert result.result_for_node("hello_world").success
        assert "notebook" in result.result_for_node("hello_world").output_values

        output_path = (
            materializations[0]
            .event_specific_data.materialization.metadata_entries[0]
            .entry_data.path
        )
        assert os.path.exists(output_path)

        assert result.result_for_node("load_notebook").success
        with open(output_path, "rb") as f:
            assert f.read() == result.result_for_node("load_notebook").output_value()
