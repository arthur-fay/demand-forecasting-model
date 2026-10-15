/** The API reads each view uses, with stable cache keys (one request per key per session). */
import { api } from '../api/endpoints';
import { useResource } from '../api/resource';

export const useSystem = () => useResource('system', () => api.system());
export const useModels = () => useResource('models', () => api.models());
export const useForecast = () => useResource('forecast', () => api.forecast());
export const useUncertaintyStatus = () =>
  useResource('uncertainty-status', () => api.uncertaintyStatus());
export const useIntervals = () => useResource('intervals', () => api.intervals());
export const useScenarios = () => useResource('scenarios', () => api.scenarios());
export const useComparison = () => useResource('comparison', () => api.comparison());
export const useSimulationParameters = () =>
  useResource('simulation-parameters', () => api.simulationParameters());

export const useScenario = (id: string | null) =>
  useResource(id === null ? null : `scenario:${id}`, () => api.scenario(id ?? 'normal'), {
    group: 'scenario',
  });

/** Observed history: `start` null means the API's default window (the last 180 days). */
export const useHistory = (start: string | null) =>
  useResource(`history:${start ?? 'default'}`, () => api.history(start === null ? {} : { start }), {
    group: 'history',
  });

export const useHorizonMetrics = (period: string) =>
  useResource(`metrics:horizon:${period}`, () => api.metrics({ scope: 'horizon', period }), {
    group: 'metrics-horizon',
  });

export const useBucketMetrics = () =>
  useResource('metrics:bucket', () => api.metrics({ scope: 'bucket' }));
