from dagster import op, job, repository
from dagster._legacy import pipeline


@op
def do_something():
    return 1


@pipeline(name="extra")
def extra_job():
    do_something()


@job
def extra_job():
    do_something()


@repository
def extra():
    return {"pipelines": {"extra": extra_job}, "jobs": {"extra_job": extra_job}}
