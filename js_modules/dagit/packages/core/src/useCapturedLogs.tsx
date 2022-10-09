import {gql, useQuery, useSubscription} from '@apollo/client';
import * as React from 'react';

import {
  CapturedLogsSubscription,
  CapturedLogsSubscriptionVariables,
  CapturedLogsSubscription_capturedLogs,
} from './types/CapturedLogsSubscription';

export const MAX_STREAMING_LOG_BYTES = 5242880; // 5 MB

const slice = (s: string) =>
  s.length < MAX_STREAMING_LOG_BYTES ? s : s.slice(-MAX_STREAMING_LOG_BYTES);

const merge = (a?: string | null, b?: string | null): string | null => {
  if (!b) {
    return a || null;
  }
  if (!a) {
    return slice(b);
  }
  return slice(a + b);
};

interface State {
  stdout: string | null;
  stderr: string | null;
  cursor?: string | null;
  isLoading: boolean;
  stdoutDownloadUrl?: string;
  stdoutLocation?: string;
  stderrDownloadUrl?: string;
  stderrLocation?: string;
}

type Action =
  | {type: 'update'; logData: CapturedLogsSubscription_capturedLogs | null}
  | {type: 'metadata'; metadata: any};

const reducer = (state: State, action: Action): State => {
  switch (action.type) {
    case 'update':
      return {
        ...state,
        isLoading: false,
        cursor: action.logData?.cursor,
        stdout: merge(state.stdout, action.logData?.stdout),
        stderr: merge(state.stderr, action.logData?.stderr),
      };
    case 'metadata':
      return {
        ...state,
        ...action.metadata,
      };
    default:
      return state;
  }
};

const initialState: State = {
  stdout: null,
  stderr: null,
  cursor: null,
  isLoading: true,
};

export const useCapturedLogs = (logKey: string[]) => {
  const [state, dispatch] = React.useReducer(reducer, initialState);

  useSubscription<CapturedLogsSubscription, CapturedLogsSubscriptionVariables>(
    CAPTURED_LOGS_SUBSCRIPTION,
    {
      fetchPolicy: 'no-cache',
      variables: {logKey, cursor: null},
      onSubscriptionData: ({subscriptionData}) => {
        dispatch({type: 'update', logData: subscriptionData.data?.capturedLogs || null});
      },
    },
  );

  const queryResult = useQuery(CAPTURED_LOGS_METADATA_QUERY, {
    variables: {logKey},
    fetchPolicy: 'cache-and-network',
  });

  React.useEffect(() => {
    if (queryResult.data) {
      dispatch({type: 'metadata', metadata: queryResult.data.capturedLogsMetadata});
    }
  }, [queryResult.data]);

  return state;
};

const CAPTURED_LOGS_SUBSCRIPTION = gql`
  subscription CapturedLogsSubscription($logKey: [String!]!, $cursor: String) {
    capturedLogs(logKey: $logKey, cursor: $cursor) {
      stdout
      stderr
      cursor
    }
  }
`;

const CAPTURED_LOGS_METADATA_QUERY = gql`
  query CapturedLogsMetadataQuery($logKey: [String!]!) {
    capturedLogsMetadata(logKey: $logKey) {
      stdoutDownloadUrl
      stdoutLocation
      stderrDownloadUrl
      stderrLocation
    }
  }
`;
