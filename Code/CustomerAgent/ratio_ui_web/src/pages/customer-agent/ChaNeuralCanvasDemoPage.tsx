/**
 * ChaNeuralCanvasDemoPage -- Demo companion to the Live Neural Canvas.
 *
 * This page is intentionally a thin wrapper around `ChaNeuralCanvasPage`
 * (the Live Neural Canvas). Both routes render the exact same component
 * tree so any UI/UX change to the Live page is automatically mirrored
 * here. The only difference is the `NeuralCanvasMode` context value:
 *
 *   - Live route (`/customer-agent/neural-canvas`)        -> 'live'
 *   - Demo route (`/customer-agent/neural-canvas-demo`)   -> 'mock'
 *
 * Future work: branch internal data hooks on `useNeuralCanvasMode()` so
 * the demo page replays a deterministic, perfectly-timed scenario
 * (agent reasoning, action plans, sandbox execution, progress bars,
 * topology) suitable for demo video capture. Until then the demo is a
 * visual twin of the live page.
 */
import ChaNeuralCanvasPage from './ChaNeuralCanvasPage';
import { NeuralCanvasModeContext } from './neuralCanvasMode';

export default function ChaNeuralCanvasDemoPage() {
  return (
    <NeuralCanvasModeContext.Provider value="mock">
      <ChaNeuralCanvasPage />
    </NeuralCanvasModeContext.Provider>
  );
}
