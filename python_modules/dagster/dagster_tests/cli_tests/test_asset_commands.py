import pytest
from click.testing import CliRunner

from dagster import job, op, AssetKey, AssetMaterialization, Output
from dagster._cli.asset import asset_wipe_command
from dagster._core.instance import DagsterInstance
from dagster._seven import json


@pytest.fixture(name="asset_instance")
def mock_asset_instance(mocker):
    # can use the ephemeral instance, since the default InMemoryEventLogStorage is asset aware
    instance = DagsterInstance.ephemeral()
    mocker.patch(
        "dagster._core.instance.DagsterInstance.get",
        return_value=instance,
    )
    yield instance


@op
def op_one(_):
    yield AssetMaterialization(asset_key=AssetKey("asset_1"))
    yield Output(1)


@op
def op_two(_):
    yield AssetMaterialization(asset_key=AssetKey("asset_2"))
    yield AssetMaterialization(asset_key=AssetKey(["path", "to", "asset_3"]))
    yield AssetMaterialization(asset_key=AssetKey(("path", "to", "asset_4")))
    yield Output(1)


@op
def op_normalization(_):
    yield AssetMaterialization(asset_key="path/to-asset_5")
    yield Output(1)


@job
def job_one():
    op_one()


@job
def job_two():
    op_one()
    op_two()


def test_asset_wipe_errors(asset_instance):  # pylint: disable=unused-argument
    runner = CliRunner()
    result = runner.invoke(asset_wipe_command)
    assert result.exit_code == 2
    assert (
        "Error, you must specify an asset key or use `--all` to wipe all asset keys."
        in result.output
    )

    result = runner.invoke(
        asset_wipe_command, ["--all", json.dumps(["path", "to", "asset_key"])]
    )
    assert result.exit_code == 2
    assert "Error, cannot use more than one of: asset key, `--all`." in result.output


def test_asset_exit(asset_instance):  # pylint: disable=unused-argument
    runner = CliRunner()
    result = runner.invoke(asset_wipe_command, ["--all"], input="NOT_DELETE\n")
    assert result.exit_code == 0
    assert "Exiting without removing asset indexes" in result.output


def test_asset_single_wipe(asset_instance):
    runner = CliRunner()
    job_one.execute_in_process(instance=asset_instance)
    job_two.execute_in_process(instance=asset_instance)
    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 4

    result = runner.invoke(
        asset_wipe_command, [json.dumps(["path", "to", "asset_3"])], input="DELETE\n"
    )
    assert result.exit_code == 0
    assert "Removed asset indexes from event logs" in result.output

    result = runner.invoke(
        asset_wipe_command, [json.dumps(["path", "to", "asset_4"])], input="DELETE\n"
    )
    assert result.exit_code == 0
    assert "Removed asset indexes from event logs" in result.output

    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 2


def test_asset_multi_wipe(asset_instance):
    runner = CliRunner()
    job_one.execute_in_process(instance=asset_instance)
    job_two.execute_in_process(instance=asset_instance)
    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 4

    result = runner.invoke(
        asset_wipe_command,
        [json.dumps(["path", "to", "asset_3"]), json.dumps(["asset_1"])],
        input="DELETE\n",
    )
    assert result.exit_code == 0
    assert "Removed asset indexes from event logs" in result.output
    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 2


def test_asset_wipe_all(asset_instance):
    runner = CliRunner()
    job_one.execute_in_process(instance=asset_instance)
    job_two.execute_in_process(instance=asset_instance)
    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 4

    result = runner.invoke(asset_wipe_command, ["--all"], input="DELETE\n")
    assert result.exit_code == 0
    assert "Removed asset indexes from event logs" in result.output
    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 0


def test_asset_single_wipe_noprompt(asset_instance):
    runner = CliRunner()
    job_one.execute_in_process(instance=asset_instance)
    job_two.execute_in_process(instance=asset_instance)
    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 4

    result = runner.invoke(
        asset_wipe_command, ["--noprompt", json.dumps(["path", "to", "asset_3"])]
    )
    assert result.exit_code == 0
    assert "Removed asset indexes from event logs" in result.output

    asset_keys = asset_instance.all_asset_keys()
    assert len(asset_keys) == 3
