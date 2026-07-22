import * as THREE from "three";

export interface SatelliteMeta {
  imageFile: string;
  imageWidth: number;
  imageHeight: number;
  bbox: { minLng: number; maxLng: number; minLat: number; maxLat: number };
  mercator: boolean;
}

export interface GeoOrigin {
  lat: number;
  lng: number;
}

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;
const GRID_SEGMENTS = 64;

export function satelliteUvToLatLng(
  u: number,
  v: number,
  meta: SatelliteMeta,
): { lat: number; lng: number } {
  const lng = meta.bbox.minLng + u * (meta.bbox.maxLng - meta.bbox.minLng);
  if (!meta.mercator) {
    return { lat: meta.bbox.maxLat + v * (meta.bbox.minLat - meta.bbox.maxLat), lng };
  }
  const northY = Math.asinh(Math.tan(meta.bbox.maxLat * DEG));
  const southY = Math.asinh(Math.tan(meta.bbox.minLat * DEG));
  const lat = Math.atan(Math.sinh(northY + v * (southY - northY))) * RAD;
  return { lat, lng };
}

export function latLngToDiagnosticLocal(lat: number, lng: number, origin: GeoOrigin) {
  return {
    x: (lng - origin.lng) * 111320 * Math.cos(origin.lat * DEG),
    z: -(lat - origin.lat) * 111320,
  };
}

export class ScanSatelliteOverlay {
  private readonly mesh: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
  private readonly textures = new Map<string, THREE.Texture>();
  private requestedVisible = false;

  constructor(
    scene: THREE.Scene,
    private readonly origin: GeoOrigin,
    private readonly meta: SatelliteMeta,
    y: number,
  ) {
    const material = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.55,
      depthTest: false,
      depthWrite: false,
      side: THREE.DoubleSide,
      fog: false,
      toneMapped: false,
    });
    this.mesh = new THREE.Mesh(this.buildGeometry(y), material);
    this.mesh.name = "diagnostic-satellite-overlay";
    this.mesh.renderOrder = 40;
    this.mesh.visible = false;
    scene.add(this.mesh);
  }

  async setSource(url: string) {
    let texture = this.textures.get(url);
    if (!texture) {
      texture = await new THREE.TextureLoader().loadAsync(url);
      texture.flipY = false;
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.anisotropy = 16;
      texture.minFilter = THREE.LinearMipmapLinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.needsUpdate = true;
      this.textures.set(url, texture);
    }
    this.mesh.material.map = texture;
    this.mesh.material.needsUpdate = true;
    this.mesh.visible = this.requestedVisible;
  }

  setVisible(visible: boolean) {
    this.requestedVisible = visible;
    this.mesh.visible = visible && this.mesh.material.map !== null;
  }

  setOpacity(opacity: number) {
    this.mesh.material.opacity = THREE.MathUtils.clamp(opacity, 0.05, 0.9);
  }

  getBounds() {
    return new THREE.Box3().setFromObject(this.mesh);
  }

  private buildGeometry(y: number) {
    const positions: number[] = [];
    const uvs: number[] = [];
    const indices: number[] = [];
    for (let row = 0; row <= GRID_SEGMENTS; row += 1) {
      const v = row / GRID_SEGMENTS;
      for (let column = 0; column <= GRID_SEGMENTS; column += 1) {
        const u = column / GRID_SEGMENTS;
        const geo = satelliteUvToLatLng(u, v, this.meta);
        const local = latLngToDiagnosticLocal(geo.lat, geo.lng, this.origin);
        positions.push(local.x, y, local.z);
        uvs.push(u, v);
      }
    }
    const rowSize = GRID_SEGMENTS + 1;
    for (let row = 0; row < GRID_SEGMENTS; row += 1) {
      for (let column = 0; column < GRID_SEGMENTS; column += 1) {
        const topLeft = row * rowSize + column;
        const topRight = topLeft + 1;
        const bottomLeft = topLeft + rowSize;
        const bottomRight = bottomLeft + 1;
        indices.push(topLeft, bottomLeft, topRight, topRight, bottomLeft, bottomRight);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    return geometry;
  }
}
