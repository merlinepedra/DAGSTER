import {gql, useQuery, useSubscription, QueryResult} from '@apollo/client';
import {Box, ButtonLink, Colors} from '@dagster-io/ui';
import * as React from 'react';
import {useHistory} from 'react-router-dom';
import styled from 'styled-components/macro';

import {SharedToaster} from '../app/DomUtils';
import {WebSocketContext} from '../app/WebSocketProvider';
import {StatusAndMessage} from '../instance/DeploymentStatusType';
import {RepositoryLocationLoadStatus} from '../types/globalTypes';
import {WorkspaceContext} from '../workspace/WorkspaceContext';

import {CodeLocationEntryFragment} from './types/CodeLocationEntryFragment';
import {CodeLocationStatusQuery} from './types/CodeLocationStatusQuery';
import {CodeLocationSubscription} from './types/CodeLocationSubscription';

const POLL_INTERVAL = 3 * 1000;

interface CodeLocationState {
  locationEntries: CodeLocationEntryFragment[] | null;
  isLoading: boolean;
}

const initialState: CodeLocationState = {
  locationEntries: null,
  isLoading: false,
};
type Action =
  | {type: 'update'; locationEntries: CodeLocationEntryFragment[]}
  | {type: 'loading'; value: boolean};

const reducer = (state: CodeLocationState, action: Action): CodeLocationState => {
  switch (action.type) {
    case 'loading':
      return {
        ...state,
        isLoading: action.value,
      };
    case 'update':
      return {
        ...state,
        locationEntries: action.locationEntries,
      };
    default:
      return state;
  }
};

const useOnCodeLocationEntriesChange = (setShowSpinner: (value: boolean) => void) => {
  const {refetch} = React.useContext(WorkspaceContext);
  const history = useHistory();

  const reloadWorkspaceQuietly = React.useCallback(async () => {
    setShowSpinner(true);
    await refetch();
    setShowSpinner(false);
  }, [refetch, setShowSpinner]);

  const reloadWorkspaceLoudly = React.useCallback(async () => {
    setShowSpinner(true);
    const result = await refetch();
    setShowSpinner(false);

    const anyErrors =
      result.data.workspaceOrError.__typename === 'PythonError' ||
      result.data.workspaceOrError.locationEntries.some(
        (entry) => entry.locationOrLoadError?.__typename === 'PythonError',
      );

    if (anyErrors) {
      SharedToaster.show({
        intent: 'warning',
        message: (
          <Box flex={{direction: 'row', justifyContent: 'space-between', gap: 24, grow: 1}}>
            <div>Workspace loaded with errors</div>
            <ViewButton onClick={() => history.push('/code-locations')} color={Colors.White}>
              View
            </ViewButton>
          </Box>
        ),
        icon: 'check_circle',
      });
    } else {
      SharedToaster.show({
        intent: 'success',
        message: (
          <Box flex={{direction: 'row', justifyContent: 'space-between', gap: 24, grow: 1}}>
            <div>Definitions reloaded</div>
            <ViewButton onClick={() => history.push('/locations')} color={Colors.White}>
              View
            </ViewButton>
          </Box>
        ),
        icon: 'check_circle',
      });
    }
  }, [history, refetch, setShowSpinner]);

  const onCodeLocationEntriesChange = React.useCallback(
    async (
      previousEntries: CodeLocationEntryFragment[] | null,
      currentEntries: CodeLocationEntryFragment[],
    ) => {
      const entryIsLoading = (entry: CodeLocationEntryFragment) =>
        entry.loadStatus === RepositoryLocationLoadStatus.LOADING;
      const currentlyLoading = currentEntries.filter(entryIsLoading);
      const anyCurrentlyLoading = currentEntries.some(entryIsLoading);
      if (!previousEntries) {
        setShowSpinner(false);
        return;
      }
      if (previousEntries.length > currentEntries.length) {
        reloadWorkspaceQuietly();
        return;
      }
      const anyPreviouslyLoading = previousEntries.some(entryIsLoading);
      if (currentEntries.length > previousEntries.length && !anyCurrentlyLoading) {
        const previousMap: {[id: string]: true} = previousEntries.reduce(
          (accum, {id}) => ({...accum, [id]: true}),
          {},
        );

        // Count the number of new code locations.
        const addedEntries: string[] = [];
        currentEntries.forEach(({id}) => {
          if (!previousMap.hasOwnProperty(id)) {
            addedEntries.push(id);
          }
        });

        SharedToaster.show({
          intent: 'primary',
          message: (
            <Box flex={{direction: 'row', justifyContent: 'space-between', gap: 24, grow: 1}}>
              {addedEntries.length === 1 ? (
                <span>
                  Code location <strong>{addedEntries[0]}</strong> added
                </span>
              ) : (
                <span>{addedEntries.length} code locations added</span>
              )}
              <ViewButton onClick={() => history.push('/locations')} color={Colors.White}>
                View
              </ViewButton>
            </Box>
          ),
          icon: 'add_circle',
        });

        reloadWorkspaceLoudly();
        return;
      }
      if (!anyPreviouslyLoading && anyCurrentlyLoading) {
        setShowSpinner(true);
        SharedToaster.show({
          intent: 'primary',
          message: (
            <Box flex={{direction: 'row', justifyContent: 'space-between', gap: 24, grow: 1}}>
              {currentlyLoading.length === 1 ? (
                <span>
                  Updating <strong>{currentlyLoading[0].id}</strong>
                </span>
              ) : (
                <span>Updating {currentlyLoading.length} code locations</span>
              )}
              <ViewButton onClick={() => history.push('/code-locations')} color={Colors.White}>
                View
              </ViewButton>
            </Box>
          ),
          icon: 'refresh',
        });

        return;
      }
      if (anyPreviouslyLoading && !anyCurrentlyLoading) {
        reloadWorkspaceLoudly();
      }
      setShowSpinner(false);
    },
    [history, reloadWorkspaceLoudly, reloadWorkspaceQuietly, setShowSpinner],
  );

  return onCodeLocationEntriesChange;
};

const CodeLocationStatusQueryProvider = ({
  children,
}: {
  children: (result: CodeLocationState) => React.ReactChild;
}) => {
  const [state, dispatch] = React.useReducer(reducer, initialState);
  const setShowSpinner = React.useCallback(
    (value: boolean) => {
      dispatch({type: 'loading', value});
    },
    [dispatch],
  );
  const onCodeLocationEntriesChange = useOnCodeLocationEntriesChange(setShowSpinner);

  // allow query result to be undefined, so that hot reloading in dev mode doesn't error with
  // undefined errors inside of the onCompleted handler
  let queryResult: QueryResult<CodeLocationStatusQuery> | undefined = undefined;
  queryResult = useQuery<CodeLocationStatusQuery>(CODE_LOCATION_STATUS_QUERY, {
    notifyOnNetworkStatusChange: true,
    pollInterval: POLL_INTERVAL,
    onCompleted: (data: CodeLocationStatusQuery) => {
      // We have to stop polling in order to update the `after` value.
      queryResult && queryResult.stopPolling();
      if (
        data.workspaceOrError.__typename === 'Workspace' &&
        data.workspaceOrError.locationEntries
      ) {
        const previousEntries = state.locationEntries;
        const currentEntries = data.workspaceOrError.locationEntries;
        dispatch({type: 'update', locationEntries: currentEntries});
        onCodeLocationEntriesChange(previousEntries, currentEntries);
      }
      queryResult && queryResult.startPolling(POLL_INTERVAL);
    },
  });

  return <>{children(state)}</>;
};

const CodeLocationStatusSubscriptionProvider = ({
  children,
}: {
  children: (result: CodeLocationState) => React.ReactChild;
}) => {
  const [state, dispatch] = React.useReducer(reducer, initialState);
  const setShowSpinner = React.useCallback(
    (value: boolean) => {
      dispatch({type: 'loading', value});
    },
    [dispatch],
  );
  const onCodeLocationEntriesChange = useOnCodeLocationEntriesChange(setShowSpinner);
  const onUpdate = (currentEntries: CodeLocationEntryFragment[]) => {
    const previousEntries = state.locationEntries;
    dispatch({type: 'update', locationEntries: currentEntries});
    onCodeLocationEntriesChange(previousEntries, currentEntries);
  };
  return (
    <>
      <CodeLocationSubscriptionComponent onUpdate={onUpdate} />
      {children(state)}
    </>
  );
};

export const CodeLocationStatusContext = React.createContext<CodeLocationState>(initialState);

export const CodeLocationStatusProvider: React.FC<{
  skip: boolean;
}> = (props) => {
  const {availability, disabled} = React.useContext(WebSocketContext);
  if (props.skip) {
    return (
      <CodeLocationStatusContext.Provider value={initialState}>
        {props.children}
      </CodeLocationStatusContext.Provider>
    );
  }
  const websocketsUnavailable = availability === 'unavailable' || disabled;
  const Component = websocketsUnavailable
    ? CodeLocationStatusQueryProvider
    : CodeLocationStatusSubscriptionProvider;
  return (
    <Component>
      {(state: CodeLocationState) => (
        <CodeLocationStatusContext.Provider value={state}>
          {props.children}
        </CodeLocationStatusContext.Provider>
      )}
    </Component>
  );
};

export const CodeLocationSubscriptionComponent: React.FC<{
  onUpdate: (entries: CodeLocationEntryFragment[]) => void;
}> = React.memo(({onUpdate}) => {
  useSubscription<CodeLocationSubscription>(CODE_LOCATION_SUBSCRIPTION, {
    fetchPolicy: 'no-cache',
    onSubscriptionData: ({subscriptionData}) => {
      if (subscriptionData.data) {
        onUpdate(subscriptionData.data.codeLocationStatus.locationEntries);
      }
    },
  });

  return null;
});

const CODE_LOCATION_ENTRY_FRAGMENT = gql`
  fragment CodeLocationEntryFragment on WorkspaceLocationEntry {
    __typename
    id
    loadStatus
  }
`;

const CODE_LOCATION_SUBSCRIPTION = gql`
  subscription CodeLocationSubscription {
    codeLocationStatus {
      locationEntries {
        id
        ...CodeLocationEntryFragment
      }
    }
  }
  ${CODE_LOCATION_ENTRY_FRAGMENT}
`;

const CODE_LOCATION_STATUS_QUERY = gql`
  query CodeLocationStatusQuery {
    workspaceOrError {
      __typename
      ... on Workspace {
        locationEntries {
          id
          ...CodeLocationEntryFragment
        }
      }
    }
  }
  ${CODE_LOCATION_ENTRY_FRAGMENT}
`;

const ViewButton = styled(ButtonLink)`
  white-space: nowrap;
`;

export const useCodeLocationsStatus = (): StatusAndMessage | null => {
  const {locationEntries} = React.useContext(WorkspaceContext);
  const {isLoading} = React.useContext(CodeLocationStatusContext);
  if (isLoading) {
    return {
      type: 'spinner',
      content: <div>Loading definitions…</div>,
    };
  }
  const repoErrors = locationEntries.filter(
    (locationEntry) => locationEntry.locationOrLoadError?.__typename === 'PythonError',
  );
  if (repoErrors.length) {
    return {
      type: 'warning',
      content: (
        <div style={{whiteSpace: 'nowrap'}}>{`${repoErrors.length} ${
          repoErrors.length === 1 ? 'code location failed to load' : 'code locations failed to load'
        }`}</div>
      ),
    };
  }
  return null;
};
