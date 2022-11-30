import * as React from 'react';

import {useCodeLocationsStatus, CodeLocationStatusProvider} from './CodeLocationProvider';
import {StatusAndMessage} from './DeploymentStatusType';
import {useDaemonStatus} from './useDaemonStatus';

export type DeploymentStatusType = 'code-locations' | 'daemons';

type DeploymentStatus = {
  codeLocations: StatusAndMessage | null;
  daemons: StatusAndMessage | null;
};

export const DeploymentStatusContext = React.createContext<DeploymentStatus>({
  codeLocations: null,
  daemons: null,
});

interface Props {
  include: Set<DeploymentStatusType>;
}

export const DeploymentStatusProvider: React.FC<Props> = (props) => {
  const {include} = props;
  const skipCodeLocations = !include.has('code-locations');
  return (
    <CodeLocationStatusProvider skip={skipCodeLocations}>
      <DeploymentStatusProviderWrapped {...props} />
    </CodeLocationStatusProvider>
  );
};

const DeploymentStatusProviderWrapped: React.FC<Props> = (props) => {
  const {children, include} = props;
  const codeLocations = useCodeLocationsStatus();
  const daemons = useDaemonStatus(!include.has('daemons'));

  const value = React.useMemo(() => ({codeLocations, daemons}), [daemons, codeLocations]);

  return (
    <DeploymentStatusContext.Provider value={value}>{children}</DeploymentStatusContext.Provider>
  );
};
