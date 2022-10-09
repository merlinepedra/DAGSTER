import {Colors, Icon} from '@dagster-io/ui';
import * as React from 'react';

import {RawLogContent} from './RawLogContent';
import {AppContext} from './app/AppContext';
import {useCapturedLogs} from './useCapturedLogs';

interface CapturedLogProps {
  logKey: string[];
  visibleIOType: string;
  onSetDownloadUrl?: (url: string) => void;
}

interface CapturedOrExternalLogPanelProps extends CapturedLogProps {
  externalUrl?: string;
}

export const CapturedOrExternalLogPanel: React.FC<CapturedOrExternalLogPanelProps> = React.memo(
  ({externalUrl, ...props}) => {
    if (externalUrl) {
      return (
        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'row',
            backgroundColor: Colors.Gray900,
            color: Colors.White,
            justifyContent: 'center',
            alignItems: 'center',
          }}
        >
          View logs at
          <a
            href={externalUrl}
            target="_blank"
            rel="noreferrer"
            style={{
              color: Colors.White,
              textDecoration: 'underline',
              marginLeft: 4,
              marginRight: 4,
            }}
          >
            {externalUrl}
          </a>
          <Icon name="open_in_new" color={Colors.White} size={20} style={{marginTop: 2}} />
        </div>
      );
    }
    return <CapturedLogPanel {...props} />;
  },
);

export const CapturedLogPanel: React.FC<CapturedLogProps> = React.memo(
  ({logKey, visibleIOType, onSetDownloadUrl}) => {
    const {rootServerURI} = React.useContext(AppContext);
    const {
      isLoading,
      stdout,
      stderr,
      stdoutLocation,
      stderrLocation,
      stderrDownloadUrl,
      stdoutDownloadUrl,
    } = useCapturedLogs(logKey);
    React.useEffect(() => {
      const visibleDownloadUrl = visibleIOType === 'stdout' ? stdoutDownloadUrl : stderrDownloadUrl;
      if (!onSetDownloadUrl || !visibleDownloadUrl) {
        return;
      }
      if (visibleDownloadUrl.startsWith('/')) {
        onSetDownloadUrl(rootServerURI + visibleDownloadUrl);
      } else {
        onSetDownloadUrl(visibleDownloadUrl);
      }
    }, [onSetDownloadUrl, visibleIOType, stderrDownloadUrl, stdoutDownloadUrl, rootServerURI]);
    return (
      <div style={{flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column'}}>
        <RawLogContent
          logData={stdout}
          isLoading={isLoading}
          location={stdoutLocation}
          isVisible={visibleIOType === 'stdout'}
        />
        <RawLogContent
          logData={stderr}
          isLoading={isLoading}
          location={stderrLocation}
          isVisible={visibleIOType === 'stderr'}
        />
      </div>
    );
  },
);
