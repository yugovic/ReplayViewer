/**
 * Effects – post-processing pipeline using the `postprocessing` package.
 *
 * Quality presets:
 *   high : Bloom + SSAO + SMAA + Vignette
 *   low  : Bloom + SMAA only
 *
 * Usage:
 *   const fx = new Effects(renderer, scene, camera, 'high');
 *   fx.render();          // call instead of renderer.render()
 *   fx.setSize(w, h);
 *   fx.dispose();
 */

import * as THREE from "three";
import {
  EffectComposer,
  EffectPass,
  RenderPass,
  BloomEffect,
  SMAAEffect,
  VignetteEffect,
  SSAOEffect,
  BlendFunction,
} from "postprocessing";

export type QualityPreset = "high" | "low";

export class Effects {
  private composer: EffectComposer;
  private disposed = false;

  constructor(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
    quality: QualityPreset = "high",
  ) {
    this.composer = new EffectComposer(renderer);

    const renderPass = new RenderPass(scene, camera);
    this.composer.addPass(renderPass);

    const bloom = new BloomEffect({
      luminanceThreshold: 0.62,
      luminanceSmoothing: 0.08,
      intensity: 1.8,
      blendFunction: BlendFunction.ADD,
    });

    const smaa = new SMAAEffect();

    if (quality === "high") {
      const ssao = new SSAOEffect(camera as THREE.PerspectiveCamera, undefined, {
        blendFunction: BlendFunction.MULTIPLY,
        samples: 16,
        rings: 4,
        radius: 3,
        bias: 0.05,
        intensity: 1.2,
      });
      const vignette = new VignetteEffect({
        offset: 0.42,
        darkness: 0.72,
      });
      this.composer.addPass(new EffectPass(camera, bloom, ssao, smaa, vignette));
    } else {
      this.composer.addPass(new EffectPass(camera, bloom, smaa));
    }
  }

  render(deltaTime?: number): void {
    if (!this.disposed) {
      this.composer.render(deltaTime);
    }
  }

  setSize(width: number, height: number): void {
    this.composer.setSize(width, height);
  }

  dispose(): void {
    this.disposed = true;
    this.composer.dispose();
  }
}
