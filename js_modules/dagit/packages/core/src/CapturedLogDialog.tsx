import {Box, Button, Tabs, Tab, DialogFooter, Dialog} from '@dagster-io/ui';
import * as React from 'react';

import {CapturedLogPanel} from './CapturedLogPanel';

export const CapturedLogDialog: React.FC<{
  logKey?: string[];
  onClose: () => void;
}> = ({logKey, onClose}) => {
  const [logType, setLogType] = React.useState<'stdout' | 'stderr'>('stdout');
  if (!logKey) {
    return null;
  }
  return (
    <Dialog
      isOpen={!!logKey}
      onClose={onClose}
      style={{width: '90vw', height: '90vh', display: 'flex'}}
    >
      <Box padding={{vertical: 8, horizontal: 16}}>
        <Tabs selectedTabId={logType} onChange={setLogType} size="small">
          <Tab id="stdout" title="stdout" />
          <Tab id="stderr" title="stderr" />
        </Tabs>
      </Box>
      <CapturedLogPanel logKey={logKey} visibleIOType={logType} />
      <DialogFooter>
        <Button intent="primary" onClick={onClose}>
          OK
        </Button>
      </DialogFooter>
    </Dialog>
  );
};
