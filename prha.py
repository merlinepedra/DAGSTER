from dagster import build_sensor_context, DagsterInstance, op, job, sensor
from dagster._utils.log import configure_loggers


@op
def my_op():
    pass

@job
def my_job():
    my_op()

@sensor(job=my_job)
def prha_sensor(context):
    # context.log.info('hiiiii')
    # context.log.info('world')
    yield SkipReason(f"Do nothing")

if __name__ == '__main__':
    configure_loggers()
    instance = DagsterInstance.get()
    with build_sensor_context(
        instance=instance,
        repository_name="blah",
        sensor_name="prha_sensor"
    ) as context:
        context.log.info('hiiiii')
        context.log.info('world')