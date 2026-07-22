/** three.js scene shell: renderer, top-down ortho / orbit perspective cameras, picking. */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

export type ViewMode = "top" | "orbit";

export class EditorScene {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly container: HTMLElement;
  viewMode: ViewMode = "top";

  private ortho: THREE.OrthographicCamera;
  private persp: THREE.PerspectiveCamera;
  controls: OrbitControls;
  private raycaster = new THREE.Raycaster();
  private center = new THREE.Vector3();
  private radius = 500;
  private frameCbs: (() => void)[] = [];

  constructor(container: HTMLElement) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);

    this.scene.background = new THREE.Color(0x10141c);
    this.scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x30281e, 1.2));
    const sun = new THREE.DirectionalLight(0xfff2dd, 1.8);
    sun.position.set(300, 500, 200);
    this.scene.add(sun);

    this.ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, -2000, 4000);
    this.persp = new THREE.PerspectiveCamera(55, 1, 0.5, 20000);
    this.controls = new OrbitControls(this.ortho, this.renderer.domElement);

    addEventListener("resize", () => this.resize());
    this.resize();
    this.renderer.setAnimationLoop(() => {
      this.controls.update();
      for (const cb of this.frameCbs) cb();
      this.renderer.render(this.scene, this.camera);
    });
  }

  get camera(): THREE.Camera {
    return this.viewMode === "top" ? this.ortho : this.persp;
  }

  onFrame(cb: () => void) {
    this.frameCbs.push(cb);
  }

  /** World-units-per-pixel at the focus point (for constant-screen-size handles). */
  worldPerPixel(): number {
    const h = this.container.clientHeight || 1;
    if (this.viewMode === "top") {
      return (this.ortho.top - this.ortho.bottom) / (this.ortho.zoom * h);
    }
    const dist = this.persp.position.distanceTo(this.controls.target);
    return (2 * dist * Math.tan((this.persp.fov * Math.PI) / 360)) / h;
  }

  fit(center: THREE.Vector3, radius: number) {
    this.center.copy(center);
    this.radius = radius;
    this.applyView();
  }

  setViewMode(mode: ViewMode) {
    if (mode === this.viewMode) return;
    // Keep the current focus point when switching.
    this.center.copy(this.controls.target);
    this.viewMode = mode;
    this.applyView();
  }

  private applyView() {
    const c = this.center;
    const r = this.radius;
    this.controls.dispose();
    if (this.viewMode === "top") {
      this.ortho.position.set(c.x, c.y + r * 2, c.z);
      this.ortho.up.set(0, 0, -1); // north (−z) up on screen
      this.ortho.zoom = 1;
      this.ortho.lookAt(c);
      this.controls = new OrbitControls(this.ortho, this.renderer.domElement);
      this.controls.enableRotate = false;
      this.controls.screenSpacePanning = true;
    } else {
      this.persp.position.set(c.x + r * 0.7, c.y + r * 0.9, c.z + r * 1.1);
      this.persp.up.set(0, 1, 0);
      this.persp.lookAt(c);
      this.controls = new OrbitControls(this.persp, this.renderer.domElement);
      this.controls.maxPolarAngle = Math.PI / 2 - 0.02;
    }
    this.controls.target.copy(c);
    this.controls.enableDamping = false;
    this.resize();
  }

  resize() {
    const w = this.container.clientWidth || 1;
    const h = this.container.clientHeight || 1;
    this.renderer.setSize(w, h);
    const aspect = w / h;
    const half = this.radius * 1.15;
    this.ortho.left = -half * aspect;
    this.ortho.right = half * aspect;
    this.ortho.top = half;
    this.ortho.bottom = -half;
    this.ortho.updateProjectionMatrix();
    this.persp.aspect = aspect;
    this.persp.updateProjectionMatrix();
  }

  private ndc(ev: PointerEvent | MouseEvent): THREE.Vector2 {
    const rect = this.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(
      ((ev.clientX - rect.left) / rect.width) * 2 - 1,
      -((ev.clientY - rect.top) / rect.height) * 2 + 1,
    );
  }

  pick(ev: PointerEvent | MouseEvent, objects: THREE.Object3D[]): THREE.Intersection | null {
    if (objects.length === 0) return null;
    this.raycaster.setFromCamera(this.ndc(ev), this.camera);
    const hits = this.raycaster.intersectObjects(objects, false);
    return hits[0] ?? null;
  }

  /**
   * Raycast against THREE.Points objects with a world-units pick threshold.
   * Returns the point NEAREST TO THE RAY (not the first along it — in a
   * top-down view several neighbouring points fall within the threshold and
   * hits[0] would be whichever sits highest, grabbing the wrong index).
   */
  pickPoints(
    ev: PointerEvent | MouseEvent,
    objects: THREE.Points[],
    thresholdWorld: number,
  ): THREE.Intersection | null {
    this.raycaster.setFromCamera(this.ndc(ev), this.camera);
    this.raycaster.params.Points.threshold = thresholdWorld;
    const hits = this.raycaster.intersectObjects(
      objects.filter((o) => o.visible),
      false,
    );
    let best: THREE.Intersection | null = null;
    for (const h of hits) {
      const d = h.distanceToRay ?? Infinity;
      if (!best || d < (best.distanceToRay ?? Infinity)) best = h;
    }
    return best;
  }

  /** Intersect the pointer ray with the horizontal plane y = planeY. */
  pickPlane(ev: PointerEvent | MouseEvent, planeY: number): THREE.Vector3 | null {
    this.raycaster.setFromCamera(this.ndc(ev), this.camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -planeY);
    const out = new THREE.Vector3();
    return this.raycaster.ray.intersectPlane(plane, out) ? out : null;
  }
}
