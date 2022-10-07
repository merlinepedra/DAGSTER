# -*- coding: utf-8 -*-
# snapshottest: v1 - https://goo.gl/zC4yUc
from __future__ import unicode_literals

from snapshottest import GenericRepr, Snapshot


snapshots = Snapshot()

snapshots['TestComputeLogs.test_compute_logs_subscription_graphql[postgres_with_default_run_launcher_deployed_grpc_env] 1'] = [
    {
        'capturedLogs': {
            'cursor': '12:881',
            'stderr': '''2022-10-10 13:07:06 -0700 - dagster - DEBUG - spew_pipeline - d307d901-98d7-4b66-b43f-927f953d20de - 2959 - LOGS_CAPTURED - Started capturing logs in process (pid: 2959).
2022-10-10 13:07:06 -0700 - dagster - DEBUG - spew_pipeline - d307d901-98d7-4b66-b43f-927f953d20de - 2959 - spew - STEP_START - Started execution of step "spew".
2022-10-10 13:07:06 -0700 - dagster - DEBUG - spew_pipeline - d307d901-98d7-4b66-b43f-927f953d20de - 2959 - spew - STEP_OUTPUT - Yielded output "result" of type "Any". (Type check passed).
2022-10-10 13:07:06 -0700 - dagster - DEBUG - spew_pipeline - d307d901-98d7-4b66-b43f-927f953d20de - 2959 - spew - HANDLED_OUTPUT - Handled output "result" using IO manager "io_manager"
2022-10-10 13:07:06 -0700 - dagster - DEBUG - spew_pipeline - d307d901-98d7-4b66-b43f-927f953d20de - 2959 - spew - STEP_SUCCESS - Finished execution of step "spew" in 16ms.
''',
            'stdout': '''HELLO WORLD
'''
        }
    }
]

snapshots['TestComputeLogs.test_compute_logs_subscription_graphql[postgres_with_default_run_launcher_managed_grpc_env] 1'] = [
    {
        'capturedLogs': {
            'cursor': '12:881',
            'stderr': '''2022-10-10 13:06:56 -0700 - dagster - DEBUG - spew_pipeline - d287e607-18c2-4248-aeb2-45bc52a8dce4 - 2569 - LOGS_CAPTURED - Started capturing logs in process (pid: 2569).
2022-10-10 13:06:56 -0700 - dagster - DEBUG - spew_pipeline - d287e607-18c2-4248-aeb2-45bc52a8dce4 - 2569 - spew - STEP_START - Started execution of step "spew".
2022-10-10 13:06:56 -0700 - dagster - DEBUG - spew_pipeline - d287e607-18c2-4248-aeb2-45bc52a8dce4 - 2569 - spew - STEP_OUTPUT - Yielded output "result" of type "Any". (Type check passed).
2022-10-10 13:06:56 -0700 - dagster - DEBUG - spew_pipeline - d287e607-18c2-4248-aeb2-45bc52a8dce4 - 2569 - spew - HANDLED_OUTPUT - Handled output "result" using IO manager "io_manager"
2022-10-10 13:06:56 -0700 - dagster - DEBUG - spew_pipeline - d287e607-18c2-4248-aeb2-45bc52a8dce4 - 2569 - spew - STEP_SUCCESS - Finished execution of step "spew" in 14ms.
''',
            'stdout': '''HELLO WORLD
'''
        }
    }
]

snapshots['TestComputeLogs.test_compute_logs_subscription_graphql[sqlite_with_default_run_launcher_deployed_grpc_env] 1'] = [
    {
        'capturedLogs': {
            'cursor': '12:881',
            'stderr': '''2022-10-10 13:06:49 -0700 - dagster - DEBUG - spew_pipeline - 1937fce8-2d62-464a-8142-c726742e63fc - 2242 - LOGS_CAPTURED - Started capturing logs in process (pid: 2242).
2022-10-10 13:06:49 -0700 - dagster - DEBUG - spew_pipeline - 1937fce8-2d62-464a-8142-c726742e63fc - 2242 - spew - STEP_START - Started execution of step "spew".
2022-10-10 13:06:49 -0700 - dagster - DEBUG - spew_pipeline - 1937fce8-2d62-464a-8142-c726742e63fc - 2242 - spew - STEP_OUTPUT - Yielded output "result" of type "Any". (Type check passed).
2022-10-10 13:06:49 -0700 - dagster - DEBUG - spew_pipeline - 1937fce8-2d62-464a-8142-c726742e63fc - 2242 - spew - HANDLED_OUTPUT - Handled output "result" using IO manager "io_manager"
2022-10-10 13:06:49 -0700 - dagster - DEBUG - spew_pipeline - 1937fce8-2d62-464a-8142-c726742e63fc - 2242 - spew - STEP_SUCCESS - Finished execution of step "spew" in 11ms.
''',
            'stdout': '''HELLO WORLD
'''
        }
    }
]

snapshots['TestComputeLogs.test_compute_logs_subscription_graphql[sqlite_with_default_run_launcher_managed_grpc_env] 1'] = [
    {
        'capturedLogs': {
            'cursor': '12:881',
            'stderr': '''2022-10-10 13:06:42 -0700 - dagster - DEBUG - spew_pipeline - 3936722d-737d-4544-aaeb-d80eb260ebbd - 2104 - LOGS_CAPTURED - Started capturing logs in process (pid: 2104).
2022-10-10 13:06:42 -0700 - dagster - DEBUG - spew_pipeline - 3936722d-737d-4544-aaeb-d80eb260ebbd - 2104 - spew - STEP_START - Started execution of step "spew".
2022-10-10 13:06:43 -0700 - dagster - DEBUG - spew_pipeline - 3936722d-737d-4544-aaeb-d80eb260ebbd - 2104 - spew - STEP_OUTPUT - Yielded output "result" of type "Any". (Type check passed).
2022-10-10 13:06:43 -0700 - dagster - DEBUG - spew_pipeline - 3936722d-737d-4544-aaeb-d80eb260ebbd - 2104 - spew - HANDLED_OUTPUT - Handled output "result" using IO manager "io_manager"
2022-10-10 13:06:43 -0700 - dagster - DEBUG - spew_pipeline - 3936722d-737d-4544-aaeb-d80eb260ebbd - 2104 - spew - STEP_SUCCESS - Finished execution of step "spew" in 11ms.
''',
            'stdout': '''HELLO WORLD
'''
        }
    }
]
