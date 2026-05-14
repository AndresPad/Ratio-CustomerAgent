/**
 * ChaNeuralCanvasLivePage -- Live companion to the Demo Neural Canvas.
 *
 * Thin wrapper around `ChaNeuralCanvasPage` that pins the
 * `NeuralCanvasMode` context to 'live'. Mirrors `ChaNeuralCanvasDemoPage`
 * (which pins it to 'mock') so the two routes share the exact same
 * component tree but differ in their data source.
 */
import ChaNeuralCanvasPage from './ChaNeuralCanvasPage';
import { NeuralCanvasModeContext } from './neuralCanvasMode';

export default function ChaNeuralCanvasLivePage() {
  return (
    <NeuralCanvasModeContext.Provider value="live">
      <ChaNeuralCanvasPage />
    </NeuralCanvasModeContext.Provider>
  );
}
