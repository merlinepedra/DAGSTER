from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)

from slack_sdk.web.client import WebClient

from dagster import DefaultSensorStatus
from dagster._core.definitions import GraphDefinition, JobDefinition
from dagster._core.definitions.run_status_sensor_definition import (
    RunFailureSensorContext,
    RunStatusSensorContext,
    run_failure_sensor,
)
from dagster._core.definitions.unresolved_asset_job_definition import UnresolvedAssetJobDefinition
from dagster._utils.backcompat import deprecation_warning

if TYPE_CHECKING:
    from dagster._core.host_representation.selector import JobSelector, RepositorySelector

T = TypeVar("T", bound=RunStatusSensorContext)


def _build_slack_blocks_and_text(
    context: T,
    text_fn: Callable[[T], str],
    blocks_fn: Optional[Callable[[T], List[Dict[Any, Any]]]],
    dagit_base_url: Optional[str],
) -> Tuple[List[Dict[str, Any]], str]:
    main_body_text = text_fn(context)
    blocks: List[Dict[Any, Any]] = []
    if blocks_fn:
        blocks.extend(blocks_fn(context))
    else:
        blocks.extend(
            [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f'*Job "{context.pipeline_run.pipeline_name}" failed. `{context.pipeline_run.run_id.split("-")[0]}`*',
                    },
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": main_body_text},
                },
            ]
        )

    if dagit_base_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Dagit"},
                        "url": f"{dagit_base_url}/instance/runs/{context.pipeline_run.run_id}",
                    }
                ],
            }
        )
    return blocks, main_body_text


def _default_failure_message_text_fn(context: RunFailureSensorContext) -> str:
    return f"Error: ```{context.failure_event.message}```"


def make_slack_on_run_failure_sensor(
    channel: str,
    slack_token: str,
    text_fn: Callable[[RunFailureSensorContext], str] = _default_failure_message_text_fn,
    blocks_fn: Optional[Callable[[RunFailureSensorContext], List[Dict[Any, Any]]]] = None,
    name: Optional[str] = None,
    dagit_base_url: Optional[str] = None,
    monitored_jobs: Optional[
        Sequence[
            Union[
                JobDefinition,
                GraphDefinition,
                UnresolvedAssetJobDefinition,
                "RepositorySelector",
                "JobSelector",
            ]
        ]
    ] = None,
    job_selection: Optional[
        Sequence[
            Union[
                JobDefinition,
                GraphDefinition,
                UnresolvedAssetJobDefinition,
                "RepositorySelector",
                "JobSelector",
            ]
        ]
    ] = None,
    default_status: DefaultSensorStatus = DefaultSensorStatus.STOPPED,
):
    """Create a sensor on job failures that will message the given Slack channel.

    Args:
        channel (str): The channel to send the message to (e.g. "#my_channel")
        slack_token (str): The slack token.
            Tokens are typically either user tokens or bot tokens. More in the Slack API
            documentation here: https://api.slack.com/docs/token-types
        text_fn (Optional(Callable[[RunFailureSensorContext], str])): Function which
            takes in the ``RunFailureSensorContext`` and outputs the message you want to send.
            Defaults to a text message that contains error message, job name, and run ID.
            The usage of the `text_fn` changes depending on whether you're using `blocks_fn`. If you
            are using `blocks_fn`, this is used as a fallback string to display in notifications. If
            you aren't, this is the main body text of the message. It can be formatted as plain text,
            or with markdown.
            See more details in https://api.slack.com/methods/chat.postMessage#text_usage
        blocks_fn (Callable[[RunFailureSensorContext], List[Dict]]): Function which takes in
            the ``RunFailureSensorContext`` and outputs the message blocks you want to send.
            See information about Blocks in https://api.slack.com/reference/block-kit/blocks
        name: (Optional[str]): The name of the sensor. Defaults to "slack_on_run_failure".
        dagit_base_url: (Optional[str]): The base url of your Dagit instance. Specify this to allow
            messages to include deeplinks to the failed job run.
        monitored_jobs (Optional[List[Union[PipelineDefinition, GraphDefinition, RepositorySelector, JobSelector]]]): The jobs in the
            current repository that will be monitored by this failure sensor. Defaults to None, which
            means the alert will be sent when any job in the repository fails. To monitor jobs in external repositories, use RepositorySelector and JobSelector
        job_selection (Optional[List[Union[PipelineDefinition, GraphDefinition, RepositorySelector, JobSelector]]]): (deprecated in favor of monitored_jobs)
            The jobs in the current repository that will be monitored by this failure sensor. Defaults to None, which means the alert will
            be sent when any job in the repository fails.
        default_status (DefaultSensorStatus): Whether the sensor starts as running or not. The default
            status can be overridden from Dagit or via the GraphQL API.

    Examples:

        .. code-block:: python

            slack_on_run_failure = make_slack_on_run_failure_sensor(
                "#my_channel",
                os.getenv("MY_SLACK_TOKEN")
            )

            @repository
            def my_repo():
                return [my_job + slack_on_run_failure]

        .. code-block:: python

            def my_message_fn(context: RunFailureSensorContext) -> str:
                return (
                    f"Job {context.pipeline_run.pipeline_name} failed!"
                    f"Error: {context.failure_event.message}"
                )

            slack_on_run_failure = make_slack_on_run_failure_sensor(
                channel="#my_channel",
                slack_token=os.getenv("MY_SLACK_TOKEN"),
                text_fn=my_message_fn,
                dagit_base_url="http://mycoolsite.com",
            )


    """

    slack_client = WebClient(token=slack_token)

    if job_selection:
        deprecation_warning("job_selection", "2.0.0", "Use monitored_jobs instead.")
    jobs = monitored_jobs if monitored_jobs else job_selection

    @run_failure_sensor(name=name, monitored_jobs=jobs, default_status=default_status)
    def slack_on_run_failure(context: RunFailureSensorContext):
        blocks, main_body_text = _build_slack_blocks_and_text(
            context=context, text_fn=text_fn, blocks_fn=blocks_fn, dagit_base_url=dagit_base_url
        )

        slack_client.chat_postMessage(channel=channel, blocks=blocks, text=main_body_text)

    return slack_on_run_failure
