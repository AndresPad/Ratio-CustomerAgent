/**
 * Neural Canvas mode context.
 *
 * The Live and Mock Neural Canvas pages share the same React component tree
 * (ChaNeuralCanvasPage) so any visual or behavioural change made for the live
 * version automatically mirrors into the mock. The two pages differ only in
 * the value provided through this context:
 *
 *   - 'live' : real orchestration data, live polling against the agents API.
 *   - 'mock' : deterministic, pre-recorded data for demo / video capture.
 *
 * Until the mock data layer is wired up, both modes render identically. When
 * we begin diverging the data sources, code paths inside ChaNeuralCanvasPage
 * (and its sub-components) should branch on `useNeuralCanvasMode()` rather
 * than forking the component.
 */
import { createContext, useContext } from 'react';

export type NeuralCanvasMode = 'live' | 'mock';

export const NeuralCanvasModeContext = createContext<NeuralCanvasMode>('live');

export function useNeuralCanvasMode(): NeuralCanvasMode {
  return useContext(NeuralCanvasModeContext);
}

export function useIsNeuralCanvasMock(): boolean {
  return useContext(NeuralCanvasModeContext) === 'mock';
}
