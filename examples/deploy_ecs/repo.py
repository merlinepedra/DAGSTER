# import os
import time

from dagster import AssetMaterialization, graph, op, repository
from dagster._core.definitions.metadata import MetadataValue


@op
def my_op(context):
    for i in range(200):
        time.sleep(0.5)
        print(f"stdout {i}")
        if i % 1000 == 420:
            context.log.error(f"Error message seq={i}")
        elif i % 100 == 0:
            context.log.warning(f"Warning message seq={i}")
        elif i % 10 == 0:
            context.log.info(f"Info message seq={i}")
        else:
            context.log.debug(f"Debug message seq={i}")
    # time.sleep(30)
    # tags = context.pipeline_run.tags
    # arn = tags.get("ecs/task_arn")
    # arn_path = arn.split(":")[-1]
    # arn_id = arn_path.split("/")[-1]
    # region = os.getenv("AWS_REGION")
    # cloudwatch_url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/$252Fdocker-compose$252Fdagster/log-events/dagster$252Frun$252F{arn_id}"

    # context.log_event(
    #     AssetMaterialization(
    #         asset_key="my_asset",
    #         metadata={
    #             **{k: v for k, v in os.environ.items()},
    #             "log_url": MetadataValue.url(cloudwatch_url),
    #         },
    #     )
    # )
    return True


@graph
def my_graph():
    my_op()


my_job = my_graph.to_job()


@repository
def repo():
    return [my_job]
