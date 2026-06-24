"""Render a Layout to a self-contained, navigable 3D HTML page (Three.js, no build)."""

from __future__ import annotations

import json

from .model import Layout

_SCENE_JS = """
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const L = window.__LAYOUT__;
const site = L.site, b = L.building;
const cx = site.width_m / 2, cz = site.depth_m / 2;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1116);
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 8000);
camera.position.set(cx, Math.max(site.width_m, site.depth_m) * 0.9, cz + site.depth_m);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(cx, 0, cz);

scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 1.1));
const dir = new THREE.DirectionalLight(0xffffff, 0.6);
dir.position.set(cx, 250, cz);
scene.add(dir);

function box(x, z, w, d, h, color, y0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color }));
  m.position.set(x + w / 2, (y0 || 0) + h / 2, z + d / 2);
  return m;
}

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(site.width_m, site.depth_m),
  new THREE.MeshStandardMaterial({ color: 0x1b2026 })
);
ground.rotation.x = -Math.PI / 2;
ground.position.set(cx, 0, cz);
scene.add(ground);

const yxs = L.yard.polygon.map(p => p[0]), yys = L.yard.polygon.map(p => p[1]);
const yx0 = Math.min(...yxs), yy0 = Math.min(...yys);
scene.add(box(yx0, yy0, Math.max(...yxs) - yx0, Math.max(...yys) - yy0, 0.1, 0x232a31));

const shell = box(b.x, b.y, b.width_m, b.depth_m, b.height_m, 0x3b4754);
shell.material.transparent = true;
shell.material.opacity = 0.16;
scene.add(shell);

const pickable = [];
for (const r of L.racks) {
  const m = box(r.x, r.y, r.width_m, r.depth_m, b.height_m * 0.8, 0x6f86b3);
  m.userData = { kind: 'rack', id: r.id, info: r.bays + ' bays x ' + r.levels + ' levels' };
  scene.add(m); pickable.push(m);
}
for (const d of L.docks) {
  const m = box(d.x - 1.5, d.y - 1.0, 3.0, 1.0, 1.4, 0x2f7fd8);
  m.userData = { kind: 'dock', id: d.id, info: 'face ' + d.face };
  scene.add(m); pickable.push(m);
}
for (const g of L.gates) {
  const m = box(g.x - g.width_m / 2, g.y, g.width_m, 0.6, 2.2, 0x2f7fd8);
  m.userData = { kind: 'gate', id: g.id, info: '' };
  scene.add(m); pickable.push(m);
}
for (const t of L.truck_paths) {
  const pts = t.points.map(p => new THREE.Vector3(p[0], 0.5, p[1]));
  scene.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: t.kind === 'in' ? 0x4cd07a : 0xd0734c })
  ));
}

const raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2();
const panel = document.getElementById('panel');
addEventListener('pointerdown', e => {
  mouse.x = (e.clientX / innerWidth) * 2 - 1;
  mouse.y = -(e.clientY / innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  const hit = raycaster.intersectObjects(pickable)[0];
  if (hit) { const u = hit.object.userData; panel.textContent = u.kind + ' ' + u.id + (u.info ? ' - ' + u.info : ''); }
});
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
"""

_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html,body{margin:0;height:100%;background:#0e1116;color:#cfd8e3;font-family:system-ui,sans-serif}
#panel{position:fixed;left:12px;top:12px;padding:8px 12px;background:rgba(20,26,32,.85);
border:1px solid #2b333d;border-radius:8px;font-size:14px}
</style>
<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}
</script></head>
<body><div id="panel">click a rack / dock / gate</div>
<script>window.__LAYOUT__ = __DATA__;</script>
<script type="module">__SCENE__</script>
</body></html>"""


def to_html(layout: Layout, *, title: str = "Warehouse 3D") -> str:
    return (
        _HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__DATA__", json.dumps(layout.to_dict()))
        .replace("__SCENE__", _SCENE_JS)
    )
