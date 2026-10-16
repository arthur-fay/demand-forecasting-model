/**
 * What this deployment allows, from `/atlas-deployment.json` (served by whatever serves
 * the dashboard: the API itself locally or through an SSH tunnel, nginx in the private
 * and public-demo profiles). The dashboard never guesses: if the descriptor cannot be
 * read, simulations stay disabled and the read-only views keep working.
 */
import { createContext, useContext } from 'react';

import { api } from './api/endpoints';
import { useResource } from './api/resource';
import type { DeploymentDescriptor } from './api/schemas';

export interface Deployment {
  readonly descriptor: DeploymentDescriptor | null;
  readonly loading: boolean;
  /** Why simulations are off (null when they are on). */
  readonly simulationsBlocked: string | null;
  readonly operationalDetail: boolean;
}

export const DEPLOYMENT_KEY = 'deployment';

export function describeBlock(
  descriptor: DeploymentDescriptor | null,
  loading: boolean,
): string | null {
  if (loading) return 'Checking what this deployment allows…';
  if (descriptor === null)
    return 'The deployment descriptor could not be read, so interactive simulations stay disabled. Every reference result remains available.';
  switch (descriptor.simulations) {
    case 'enabled':
      return null;
    case 'disabled_public_demo':
      return 'This is the public read-only demonstration: computation is not exposed, so simulations are disabled. Every reference result remains available.';
    case 'disabled_api_key':
      return 'This API protects simulations with a key, and a browser must never hold one. Run simulations through the private deployment (its proxy adds the key) or a local API without a key.';
  }
}

export function useDeploymentResource(): Deployment {
  const resource = useResource(DEPLOYMENT_KEY, () => api.deployment());
  const loading = resource.snapshot.status === 'loading' || resource.snapshot.status === 'idle';
  const descriptor = resource.data ?? null;
  return {
    descriptor,
    loading,
    simulationsBlocked: describeBlock(descriptor, loading),
    operationalDetail: descriptor?.operational_detail ?? false,
  };
}

export const DeploymentContext = createContext<Deployment>({
  descriptor: null,
  loading: true,
  simulationsBlocked: describeBlock(null, true),
  operationalDetail: false,
});

export function useDeployment(): Deployment {
  return useContext(DeploymentContext);
}
