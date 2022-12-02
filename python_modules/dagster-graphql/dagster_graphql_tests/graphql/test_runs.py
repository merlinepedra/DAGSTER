import copy

import yaml
from dagster_graphql.test.utils import (
    define_out_of_process_context,
    execute_dagster_graphql,
    infer_pipeline_selector,
    infer_repository_selector,
)
from dagster_graphql_tests.graphql.graphql_context_test_suite import (
    ExecutingGraphQLContextTestMatrix,
)

from dagster import AssetMaterialization, Output, job, op, repository
from dagster._core.definitions.pipeline_base import InMemoryPipeline
from dagster._core.execution.api import execute_run
from dagster._core.storage.pipeline_run import DagsterRunStatus
from dagster._core.storage.tags import PARENT_RUN_ID_TAG, ROOT_RUN_ID_TAG
from dagster._core.test_utils import instance_for_test
from dagster._legacy import execute_pipeline, lambda_solid, pipeline
from dagster._utils import Counter, traced_counter

RUNS_QUERY = """
query PipelineRunsRootQuery($selector: PipelineSelector!) {
  pipelineOrError(params: $selector) {
    ... on Pipeline {
      name
      pipelineSnapshotId
      runs {
        ...RunHistoryRunFragment
      }
    }
  }
}

fragment RunHistoryRunFragment on PipelineRun {
  runId
  status
  repositoryOrigin {
    repositoryLocationName
    repositoryName
    repositoryLocationMetadata {
      key
      value
    }
  }
  pipeline {
    ...on PipelineReference { name }
  }
  executionPlan {
    steps {
      key
    }
  }
  runConfig
  runConfigYaml
  mode
  canTerminate
  tags {
    key
    value
  }
}
"""

DELETE_RUN_MUTATION = """
mutation DeleteRun($runId: String!) {
  deletePipelineRun(runId: $runId) {
    __typename
    ... on DeletePipelineRunSuccess {
      runId
    }
    ... on PythonError {
      message
    }
  }
}
"""

ALL_TAGS_QUERY = """
{
  pipelineRunTags {
    ... on PipelineTagAndValues {
      key
      values
    }
  }
}
"""

ALL_RUNS_QUERY = """
{
  pipelineRunsOrError {
    ... on PipelineRuns {
      results {
        runId
        pipelineSnapshotId
        pipeline {
          __typename
          ... on PipelineReference {
            name
            solidSelection
          }
        }
      }
    }
  }
}
"""


FILTERED_RUN_QUERY = """
query PipelineRunsRootQuery($filter: RunsFilter!) {
  pipelineRunsOrError(filter: $filter) {
    ... on PipelineRuns {
      results {
        runId
      }
    }
  }
}
"""

FILTERED_RUN_COUNT_QUERY = """
query PipelineRunsRootQuery($filter: RunsFilter!) {
  pipelineRunsOrError(filter: $filter) {
    ... on PipelineRuns {
      count
    }
  }
}
"""


RUN_GROUP_QUERY = """
query RunGroupQuery($runId: ID!) {
  runGroupOrError(runId: $runId) {
    ... on RunGroup {
      __typename
      rootRunId
      runs {
        runId
        pipeline {
          __typename
          ... on PipelineReference {
            name
          }
        }
      }
    }
    ... on RunGroupNotFoundError {
      __typename
      runId
      message
    }
  }
}
"""


ALL_RUN_GROUPS_QUERY = """
{
  runGroupsOrError {
    results {
      rootRunId
      runs {
        runId
        pipelineSnapshotId
        pipeline {
          __typename
          ... on PipelineReference {
            name
            solidSelection
          }
        }
      }
    }
  }
}
"""

REPOSITORY_RUNS_QUERY = """
query RepositoryRunsQuery($repositorySelector: RepositorySelector!) {
    repositoryOrError(repositorySelector: $repositorySelector) {
        ... on Repository {
            id
            name
            pipelines {
                id
                name
                runs(limit: 10) {
                    id
                    runId
                }
            }
        }
    }
}
"""

ASSET_RUNS_QUERY = """
query AssetRunsQuery($assetKey: AssetKeyInput!) {
    assetOrError(assetKey: $assetKey) {
        ... on Asset {
            assetMaterializations {
                label
                runOrError {
                    ... on PipelineRun {
                        id
                        runId
                    }
                }
            }
        }
    }
}
"""


def _get_runs_data(result, run_id):
    for run_data in result.data["pipelineOrError"]["runs"]:
        if run_data["runId"] == run_id:
            # so caller can delete keys
            return copy.deepcopy(run_data)

    return None


class TestGetRuns(ExecutingGraphQLContextTestMatrix):
    def test_get_runs_over_graphql(self, graphql_context):
        # This include needs to be here because its inclusion screws up
        # other code in this file which reads itself to load a repo
        from .utils import sync_execute_get_run_log_data

        selector = infer_pipeline_selector(graphql_context, "multi_mode_with_resources")

        payload_one = sync_execute_get_run_log_data(
            context=graphql_context,
            variables={
                "executionParams": {
                    "selector": selector,
                    "mode": "add_mode",
                    "runConfigData": {"resources": {"op": {"config": 2}}},
                    "executionMetadata": {"tags": [{"key": "fruit", "value": "apple"}]},
                }
            },
        )
        run_id_one = payload_one["run"]["runId"]

        read_context = graphql_context

        result = execute_dagster_graphql(read_context, RUNS_QUERY, variables={"selector": selector})

        runs = result.data["pipelineOrError"]["runs"]
        assert len(runs) == 1

        tags = runs[0]["tags"]

        tags_by_key = {tag["key"]: tag["value"] for tag in tags}

        assert tags_by_key["fruit"] == "apple"

        origin = runs[0]["repositoryOrigin"]
        assert origin
        assert origin["repositoryLocationName"] == selector["repositoryLocationName"]
        assert origin["repositoryName"] == selector["repositoryName"]

        payload_two = sync_execute_get_run_log_data(
            context=graphql_context,
            variables={
                "executionParams": {
                    "selector": selector,
                    "mode": "add_mode",
                    "runConfigData": {"resources": {"op": {"config": 3}}},
                    "executionMetadata": {"tags": [{"key": "veggie", "value": "carrot"}]},
                }
            },
        )

        run_id_two = payload_two["run"]["runId"]

        result = execute_dagster_graphql(read_context, RUNS_QUERY, variables={"selector": selector})

        runs = result.data["pipelineOrError"]["runs"]
        assert len(runs) == 2

        all_tags_result = execute_dagster_graphql(read_context, ALL_TAGS_QUERY)
        tags = all_tags_result.data["pipelineRunTags"]

        tags_dict = {item["key"]: item["values"] for item in tags}

        assert tags_dict["fruit"] == ["apple"]
        assert tags_dict["veggie"] == ["carrot"]

        # delete the second run
        result = execute_dagster_graphql(
            read_context, DELETE_RUN_MUTATION, variables={"runId": run_id_two}
        )
        assert result.data["deletePipelineRun"]["__typename"] == "DeletePipelineRunSuccess"
        assert result.data["deletePipelineRun"]["runId"] == run_id_two

        # query it back out
        result = execute_dagster_graphql(read_context, RUNS_QUERY, variables={"selector": selector})

        # first is the same
        run_one_data = _get_runs_data(result, run_id_one)
        assert run_one_data

        # second is gone
        run_two_data = _get_runs_data(result, run_id_two)
        assert run_two_data is None

        # try to delete the second run again
        execute_dagster_graphql(read_context, DELETE_RUN_MUTATION, variables={"runId": run_id_two})

        result = execute_dagster_graphql(
            read_context, DELETE_RUN_MUTATION, variables={"runId": run_id_two}
        )
        assert result.data["deletePipelineRun"]["__typename"] == "RunNotFoundError"

    def test_run_config(self, graphql_context):
        # This include needs to be here because its inclusion screws up
        # other code in this file which reads itself to load a repo
        from .utils import sync_execute_get_run_log_data

        selector = infer_pipeline_selector(graphql_context, "multi_mode_with_resources")

        sync_execute_get_run_log_data(
            context=graphql_context,
            variables={
                "executionParams": {
                    "selector": selector,
                    "mode": "add_mode",
                    "runConfigData": {"resources": {"op": {"config": 2}}},
                }
            },
        )
        result = execute_dagster_graphql(
            graphql_context, RUNS_QUERY, variables={"selector": selector}
        )

        runs = result.data["pipelineOrError"]["runs"]
        assert len(runs) == 1
        run = runs[0]
        assert run["runConfig"] == {"resources": {"op": {"config": 2}}}
        assert run["runConfigYaml"] == yaml.safe_dump(
            run["runConfig"], default_flow_style=False, allow_unicode=True
        )


def get_repo_at_time_1():
    @lambda_solid
    def solid_A():
        pass

    @lambda_solid
    def solid_B():
        pass

    @pipeline
    def evolving_pipeline():
        solid_A()
        solid_B()

    @pipeline
    def foo_pipeline():
        solid_A()

    @repository
    def evolving_repo():
        return [evolving_pipeline, foo_pipeline]

    return evolving_repo


def get_repo_at_time_2():
    @lambda_solid
    def solid_A():
        pass

    @lambda_solid
    def solid_B_prime():
        pass

    @pipeline
    def evolving_pipeline():
        solid_A()
        solid_B_prime()

    @pipeline
    def bar_pipeline():
        solid_A()

    @repository
    def evolving_repo():
        return [evolving_pipeline, bar_pipeline]

    return evolving_repo


def get_asset_repo():
    @op
    def foo():
        yield AssetMaterialization(asset_key="foo", description="foo")
        yield Output(None)

    @job
    def foo_job():
        foo()

    @repository
    def asset_repo():
        return [foo_job]

    return asset_repo


def test_runs_over_time():
    with instance_for_test() as instance:

        repo_1 = get_repo_at_time_1()

        full_evolve_run_id = execute_pipeline(
            repo_1.get_pipeline("evolving_pipeline"), instance=instance
        ).run_id
        foo_run_id = execute_pipeline(repo_1.get_pipeline("foo_pipeline"), instance=instance).run_id
        evolve_a_run_id = execute_pipeline(
            repo_1.get_pipeline("evolving_pipeline").get_pipeline_subset_def({"solid_A"}),
            instance=instance,
        ).run_id
        evolve_b_run_id = execute_pipeline(
            repo_1.get_pipeline("evolving_pipeline").get_pipeline_subset_def({"solid_B"}),
            instance=instance,
        ).run_id

        with define_out_of_process_context(
            __file__, "get_repo_at_time_1", instance
        ) as context_at_time_1:
            result = execute_dagster_graphql(context_at_time_1, ALL_RUNS_QUERY)
            assert result.data

            t1_runs = {run["runId"]: run for run in result.data["pipelineRunsOrError"]["results"]}

            assert t1_runs[full_evolve_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": None,
            }

            assert t1_runs[foo_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "foo_pipeline",
                "solidSelection": None,
            }

            assert t1_runs[evolve_a_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_A"],
            }

            assert t1_runs[evolve_b_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_B"],
            }

        with define_out_of_process_context(
            __file__, "get_repo_at_time_2", instance
        ) as context_at_time_2:
            result = execute_dagster_graphql(context_at_time_2, ALL_RUNS_QUERY)
            assert result.data

            t2_runs = {run["runId"]: run for run in result.data["pipelineRunsOrError"]["results"]}

            assert t2_runs[full_evolve_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": None,
            }

            assert t2_runs[evolve_a_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_A"],
            }
            # pipeline name changed
            assert t2_runs[foo_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "foo_pipeline",
                "solidSelection": None,
            }
            # subset no longer valid - b renamed
            assert t2_runs[evolve_b_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_B"],
            }


def test_run_groups_over_time():
    with instance_for_test() as instance:
        repo_1 = get_repo_at_time_1()

        full_evolve_run_id = execute_pipeline(
            repo_1.get_pipeline("evolving_pipeline"), instance=instance
        ).run_id
        foo_run_id = execute_pipeline(repo_1.get_pipeline("foo_pipeline"), instance=instance).run_id
        evolve_a_run_id = execute_pipeline(
            repo_1.get_pipeline("evolving_pipeline").get_pipeline_subset_def({"solid_A"}),
            instance=instance,
        ).run_id
        evolve_b_run_id = execute_pipeline(
            repo_1.get_pipeline("evolving_pipeline").get_pipeline_subset_def({"solid_B"}),
            instance=instance,
        ).run_id

        with define_out_of_process_context(
            __file__, "get_repo_at_time_1", instance
        ) as context_at_time_1:

            result = execute_dagster_graphql(context_at_time_1, ALL_RUN_GROUPS_QUERY)
            assert result.data
            assert "runGroupsOrError" in result.data
            assert "results" in result.data["runGroupsOrError"]
            assert len(result.data["runGroupsOrError"]["results"]) == 4

            t1_runs = {
                run["runId"]: run
                for group in result.data["runGroupsOrError"]["results"]
                for run in group["runs"]
            }

            # test full_evolve_run_id
            assert t1_runs[full_evolve_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": None,
            }

            # test foo_run_id
            assert t1_runs[foo_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "foo_pipeline",
                "solidSelection": None,
            }

            # test evolve_a_run_id
            assert t1_runs[evolve_a_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_A"],
            }
            assert t1_runs[evolve_a_run_id]["pipelineSnapshotId"]

            # test evolve_b_run_id
            assert t1_runs[evolve_b_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_B"],
            }

        with define_out_of_process_context(
            __file__, "get_repo_at_time_2", instance
        ) as context_at_time_2:
            result = execute_dagster_graphql(context_at_time_2, ALL_RUN_GROUPS_QUERY)
            assert "runGroupsOrError" in result.data
            assert "results" in result.data["runGroupsOrError"]
            assert len(result.data["runGroupsOrError"]["results"]) == 4

            t2_runs = {
                run["runId"]: run
                for group in result.data["runGroupsOrError"]["results"]
                for run in group["runs"]
            }

            # test full_evolve_run_id
            assert t2_runs[full_evolve_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": None,
            }

            # test evolve_a_run_id
            assert t2_runs[evolve_a_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_A"],
            }
            assert t2_runs[evolve_a_run_id]["pipelineSnapshotId"]

            # names same
            assert (
                t1_runs[full_evolve_run_id]["pipeline"]["name"]
                == t2_runs[evolve_a_run_id]["pipeline"]["name"]
            )

            # snapshots differ
            assert (
                t1_runs[full_evolve_run_id]["pipelineSnapshotId"]
                != t2_runs[evolve_a_run_id]["pipelineSnapshotId"]
            )

            # pipeline name changed
            assert t2_runs[foo_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "foo_pipeline",
                "solidSelection": None,
            }
            # subset no longer valid - b renamed
            assert t2_runs[evolve_b_run_id]["pipeline"] == {
                "__typename": "PipelineSnapshot",
                "name": "evolving_pipeline",
                "solidSelection": ["solid_B"],
            }


def test_filtered_runs():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()
        run_id_1 = execute_pipeline(
            repo.get_pipeline("foo_pipeline"), instance=instance, tags={"run": "one"}
        ).run_id
        run_id_2 = execute_pipeline(
            repo.get_pipeline("foo_pipeline"), instance=instance, tags={"run": "two"}
        ).run_id
        with define_out_of_process_context(__file__, "get_repo_at_time_1", instance) as context:
            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_QUERY,
                variables={"filter": {"runIds": [run_id_1]}},
            )
            assert result.data
            run_ids = [run["runId"] for run in result.data["pipelineRunsOrError"]["results"]]
            assert len(run_ids) == 1
            assert run_ids[0] == run_id_1

            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_QUERY,
                variables={"filter": {"tags": [{"key": "run", "value": "one"}]}},
            )
            assert result.data
            run_ids = [run["runId"] for run in result.data["pipelineRunsOrError"]["results"]]
            assert len(run_ids) == 1
            assert run_ids[0] == run_id_1

            # test multiple run ids
            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_QUERY,
                variables={"filter": {"runIds": [run_id_1, run_id_2]}},
            )
            assert result.data
            run_ids = [run["runId"] for run in result.data["pipelineRunsOrError"]["results"]]
            assert len(run_ids) == 2
            assert set(run_ids) == set([run_id_1, run_id_2])


def test_filtered_runs_status():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()
        _ = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.STARTED
        ).run_id
        run_id_2 = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.FAILURE
        ).run_id
        with define_out_of_process_context(__file__, "get_repo_at_time_1", instance) as context:
            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_QUERY,
                variables={"filter": {"statuses": ["FAILURE"]}},
            )
            assert result.data
            run_ids = [run["runId"] for run in result.data["pipelineRunsOrError"]["results"]]
            assert len(run_ids) == 1
            assert run_ids[0] == run_id_2


def test_filtered_runs_multiple_statuses():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()
        _ = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.STARTED
        ).run_id
        run_id_2 = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.FAILURE
        ).run_id
        run_id_3 = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.SUCCESS
        ).run_id
        with define_out_of_process_context(__file__, "get_repo_at_time_1", instance) as context:
            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_QUERY,
                variables={"filter": {"statuses": ["FAILURE", "SUCCESS"]}},
            )
            assert result.data
            run_ids = [run["runId"] for run in result.data["pipelineRunsOrError"]["results"]]
            assert len(run_ids) == 2
            assert run_id_2 in run_ids
            assert run_id_3 in run_ids


def test_filtered_runs_multiple_filters():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()

        started_run_with_tags = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"),
            status=DagsterRunStatus.STARTED,
            tags={"foo": "bar"},
        )
        failed_run_with_tags = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"),
            status=DagsterRunStatus.FAILURE,
            tags={"foo": "bar"},
        )
        started_run_without_tags = instance.create_run_for_pipeline(
            repo.get_pipeline("foo_pipeline"),
            status=DagsterRunStatus.STARTED,
            tags={"baz": "boom"},
        )

        with define_out_of_process_context(__file__, "get_repo_at_time_1", instance) as context:
            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_QUERY,
                variables={
                    "filter": {
                        "statuses": ["STARTED"],
                        "tags": [{"key": "foo", "value": "bar"}],
                    }
                },
            )
            assert result.data
            run_ids = [run["runId"] for run in result.data["pipelineRunsOrError"]["results"]]
            assert len(run_ids) == 1
            assert started_run_with_tags.run_id in run_ids
            assert failed_run_with_tags.run_id not in run_ids
            assert started_run_without_tags.run_id not in run_ids


def test_filtered_runs_count():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()
        instance.create_run_for_pipeline(  # pylint: disable=expression-not-assigned
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.STARTED
        ).run_id
        instance.create_run_for_pipeline(  # pylint: disable=expression-not-assigned
            repo.get_pipeline("foo_pipeline"), status=DagsterRunStatus.FAILURE
        ).run_id
        with define_out_of_process_context(__file__, "get_repo_at_time_1", instance) as context:
            result = execute_dagster_graphql(
                context,
                FILTERED_RUN_COUNT_QUERY,
                variables={"filter": {"statuses": ["FAILURE"]}},
            )
            assert result.data
            count = result.data["pipelineRunsOrError"]["count"]
            assert count == 1


def test_run_group():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()
        foo_pipeline = repo.get_pipeline("foo_pipeline")
        runs = [execute_pipeline(foo_pipeline, instance=instance)]
        root_run_id = runs[-1].run_id
        for _ in range(3):
            # https://github.com/dagster-io/dagster/issues/2433
            run = instance.create_run_for_pipeline(
                foo_pipeline,
                parent_run_id=root_run_id,
                root_run_id=root_run_id,
                tags={PARENT_RUN_ID_TAG: root_run_id, ROOT_RUN_ID_TAG: root_run_id},
            )
            execute_run(InMemoryPipeline(foo_pipeline), run, instance)
            runs.append(run)

        with define_out_of_process_context(
            __file__, "get_repo_at_time_1", instance
        ) as context_at_time_1:
            result_one = execute_dagster_graphql(
                context_at_time_1,
                RUN_GROUP_QUERY,
                variables={"runId": root_run_id},
            )
            assert result_one.data["runGroupOrError"]["__typename"] == "RunGroup"

            assert len(result_one.data["runGroupOrError"]["runs"]) == 4

            result_two = execute_dagster_graphql(
                context_at_time_1,
                RUN_GROUP_QUERY,
                variables={"runId": runs[-1].run_id},
            )
            assert result_one.data["runGroupOrError"]["__typename"] == "RunGroup"
            assert len(result_two.data["runGroupOrError"]["runs"]) == 4

            assert (
                result_one.data["runGroupOrError"]["rootRunId"]
                == result_two.data["runGroupOrError"]["rootRunId"]
            )
            assert (
                result_one.data["runGroupOrError"]["runs"]
                == result_two.data["runGroupOrError"]["runs"]
            )


def test_run_group_not_found():
    with instance_for_test() as instance:
        with define_out_of_process_context(
            __file__, "get_repo_at_time_1", instance
        ) as context_at_time_1:

            result = execute_dagster_graphql(
                context_at_time_1,
                RUN_GROUP_QUERY,
                variables={"runId": "foo"},
            )
            assert result.data
            assert result.data["runGroupOrError"]
            assert result.data["runGroupOrError"]["__typename"] == "RunGroupNotFoundError"
            assert result.data["runGroupOrError"]["runId"] == "foo"
            assert result.data["runGroupOrError"][
                "message"
            ] == "Run group of run {run_id} could not be found.".format(run_id="foo")


def test_run_groups():
    with instance_for_test() as instance:
        repo = get_repo_at_time_1()
        foo_pipeline = repo.get_pipeline("foo_pipeline")

        root_run_ids = [execute_pipeline(foo_pipeline, instance=instance).run_id for i in range(3)]

        for _ in range(5):
            for root_run_id in root_run_ids:
                execute_pipeline(
                    foo_pipeline,
                    tags={PARENT_RUN_ID_TAG: root_run_id, ROOT_RUN_ID_TAG: root_run_id},
                    instance=instance,
                )

        with define_out_of_process_context(
            __file__, "get_repo_at_time_1", instance
        ) as context_at_time_1:
            result = execute_dagster_graphql(context_at_time_1, ALL_RUN_GROUPS_QUERY)

            assert result.data
            assert "runGroupsOrError" in result.data
            assert "results" in result.data["runGroupsOrError"]
            assert len(result.data["runGroupsOrError"]["results"]) == 3
            for run_group in result.data["runGroupsOrError"]["results"]:
                assert run_group["rootRunId"] in root_run_ids
                assert len(run_group["runs"]) == 6


def test_repository_batching():
    with instance_for_test() as instance:
        from .utils import sync_execute_get_run_log_data

        def _execute_run(graphql_context, pipeline_name):
            payload = sync_execute_get_run_log_data(
                context=graphql_context,
                variables={
                    "executionParams": {
                        "selector": infer_pipeline_selector(graphql_context, pipeline_name)
                    }
                },
            )
            return payload["run"]["runId"]

        with define_out_of_process_context(__file__, "get_repo_at_time_1", instance) as context:
            foo_run_ids = [_execute_run(context, "foo_pipeline") for _ in range(3)]
            evolving_run_ids = [_execute_run(context, "evolving_pipeline") for _ in range(2)]
            traced_counter.set(Counter())
            result = execute_dagster_graphql(
                context,
                REPOSITORY_RUNS_QUERY,
                variables={"repositorySelector": infer_repository_selector(context)},
            )
            assert result.data
            assert "repositoryOrError" in result.data
            assert "pipelines" in result.data["repositoryOrError"]
            pipelines = result.data["repositoryOrError"]["pipelines"]
            assert len(pipelines) == 2
            pipeline_runs = {pipeline["name"]: pipeline["runs"] for pipeline in pipelines}
            assert len(pipeline_runs["foo_pipeline"]) == 3
            assert len(pipeline_runs["evolving_pipeline"]) == 2
            assert set(foo_run_ids) == set(run["runId"] for run in pipeline_runs["foo_pipeline"])
            assert set(evolving_run_ids) == set(
                run["runId"] for run in pipeline_runs["evolving_pipeline"]
            )
            counter = traced_counter.get()
            counts = counter.counts()
            assert counts
            assert len(counts) == 1
            # We should have a single batch call to fetch run records, instead of 3 separate calls
            # to fetch run records (which is fetched to instantiate GrapheneRun)
            assert counts.get("DagsterInstance.get_run_records") == 1


def test_asset_batching():
    with instance_for_test() as instance:
        repo = get_asset_repo()
        foo_job = repo.get_job("foo_job")
        for _ in range(3):
            foo_job.execute_in_process(instance=instance)
        with define_out_of_process_context(__file__, "asset_repo", instance) as context:
            traced_counter.set(Counter())
            result = execute_dagster_graphql(
                context, ASSET_RUNS_QUERY, variables={"assetKey": {"path": ["foo"]}}
            )
            assert result.data
            assert "assetOrError" in result.data
            assert "assetMaterializations" in result.data["assetOrError"]
            materializations = result.data["assetOrError"]["assetMaterializations"]
            assert len(materializations) == 3
            counter = traced_counter.get()
            counts = counter.counts()
            assert counts
            assert counts.get("DagsterInstance.get_run_records") == 1
