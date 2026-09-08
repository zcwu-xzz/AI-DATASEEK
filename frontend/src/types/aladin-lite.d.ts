declare module 'aladin-lite' {
  interface AladinAPI {
    init: Promise<void>;
    aladin(target: string | HTMLElement, options?: Record<string, unknown>): any;
    graphicOverlay(options?: Record<string, unknown>): any;
    polygon(points: number[][], options?: Record<string, unknown>): any;
  }
  const A: AladinAPI;
  export default A;
}
