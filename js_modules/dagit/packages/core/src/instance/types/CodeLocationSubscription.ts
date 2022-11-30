/* tslint:disable */
/* eslint-disable */
// @generated
// This file was automatically generated and should not be edited.

import { RepositoryLocationLoadStatus } from "./../../types/globalTypes";

// ====================================================
// GraphQL subscription operation: CodeLocationSubscription
// ====================================================

export interface CodeLocationSubscription_codeLocationStatus_locationEntries {
  __typename: "WorkspaceLocationEntry";
  id: string;
  loadStatus: RepositoryLocationLoadStatus;
}

export interface CodeLocationSubscription_codeLocationStatus {
  __typename: "CodeLocationStatusSubscriptionPayload";
  locationEntries: CodeLocationSubscription_codeLocationStatus_locationEntries[];
}

export interface CodeLocationSubscription {
  codeLocationStatus: CodeLocationSubscription_codeLocationStatus;
}
