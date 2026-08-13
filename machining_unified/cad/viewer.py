"""在 Streamlit 中交互展示由 STEP 文件提取的真实三角网格。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st

from machining_unified.config.paths import PROJECT_ROOT

# cadquery-ocp 提供 OCP 二进制绑定；PyCharm 无法读取其完整类型存根。
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.BRep import BRep_Tool
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.BRepMesh import BRepMesh_IncrementalMesh
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.IFSelect import IFSelect_RetDone
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.STEPControl import STEPControl_Reader
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopAbs import TopAbs_FACE
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopExp import TopExp_Explorer
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopLoc import TopLoc_Location
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopoDS import TopoDS


MAX_TRIANGLES = 3_000


@lru_cache(maxsize=32)
def step_mesh_payload(source_file: str, max_triangles: int = MAX_TRIANGLES) -> dict[str, Any]:
    """读取 STEP 网格并标准化到浏览器画布的统一坐标空间。"""
    reader = STEPControl_Reader()
    if reader.ReadFile(source_file) != IFSelect_RetDone or reader.TransferRoots() == 0:
        raise ValueError("无法读取 STEP 网格")

    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, 0.6)
    triangles: list[list[float]] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        # 三角网格节点可能位于面的局部坐标系；装配 STEP 的组件通常依赖此位置变换。
        # 必须应用 TopLoc 变换后再汇总，否则模型会错位或被错误裁切。
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(TopoDS.Face_s(explorer.Current()), location)
        if triangulation is not None:
            transformation = location.Transformation()
            for index in range(1, int(triangulation.NbTriangles()) + 1):
                node_ids = triangulation.Triangle(index).Get()
                triangle: list[float] = []
                for node_id in node_ids:
                    point = triangulation.Node(node_id).Transformed(transformation)
                    triangle.extend((float(point.X()), float(point.Y()), float(point.Z())))
                triangles.append(triangle)
        explorer.Next()

    if not triangles:
        raise ValueError("STEP 中没有可显示的三角网格")
    if len(triangles) > max_triangles:
        stride = max(1, len(triangles) // max_triangles)
        triangles = triangles[::stride][:max_triangles]

    coordinates = [value for triangle in triangles for value in triangle]
    xs, ys, zs = coordinates[0::3], coordinates[1::3], coordinates[2::3]
    center = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2)
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
    normalized = [
        [round((triangle[index] - center[index % 3]) / span, 6) for index in range(9)]
        for triangle in triangles
    ]
    return {"triangles": normalized, "triangle_count": len(normalized)}


_VIEWER = st.components.v2.component(
    "step_mesh_viewer",
    html='<canvas aria-label="可旋转 STEP 三维模型"></canvas>',
    css="""
        :host { display: block; height: 100%; overflow: hidden; }
        canvas {
            width: 100%; height: 100%; min-height: 0; max-height: 100%; display: block;
            box-sizing: border-box; overflow: hidden;
            border: 1px solid var(--st-border-color); border-radius: var(--st-radius-md);
            background: var(--st-secondary-background-color); cursor: grab; touch-action: none;
        }
        canvas:active { cursor: grabbing; }
    """,
    js="""
export default function(component) {
  const { data, parentElement } = component;
  const canvas = parentElement.querySelector('canvas');
  if (!canvas || !data?.triangles?.length) return;
  const context = canvas.getContext('2d');
  let yaw = 0.65;
  let pitch = -0.45;
  let zoom = 1;
  let dragging = false;
  let previous = null;

  function rotate(x, y, z) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x1 = x * cy - z * sy;
    const z1 = x * sy + z * cy;
    return [x1, y * cp - z1 * sp, y * sp + z1 * cp];
  }

  function draw() {
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.floor(rect.width * scale));
    canvas.height = Math.max(1, Math.floor(rect.height * scale));
    context.setTransform(scale, 0, 0, scale, 0, 0);
    const width = rect.width, height = rect.height;
    context.clearRect(0, 0, width, height);
    const projected = data.triangles.map((triangle) => {
      const points = [];
      for (let i = 0; i < 9; i += 3) points.push(rotate(triangle[i], triangle[i + 1], triangle[i + 2]));
      const ux = points[1][0] - points[0][0], uy = points[1][1] - points[0][1], uz = points[1][2] - points[0][2];
      const vx = points[2][0] - points[0][0], vy = points[2][1] - points[0][1], vz = points[2][2] - points[0][2];
      const nz = ux * vy - uy * vx;
      const shade = Math.max(0.22, Math.min(0.92, 0.57 + nz * 0.9));
      return { points, depth: (points[0][2] + points[1][2] + points[2][2]) / 3, shade };
    });
    // 每次旋转后按当前投影外包范围重新计算缩放和中心。
    // 因而不论模型是细长轴、扁平板还是大型装配体，均可完整落在窗口内。
    const flatPoints = projected.flatMap((triangle) => triangle.points);
    const minX = Math.min(...flatPoints.map((point) => point[0]));
    const maxX = Math.max(...flatPoints.map((point) => point[0]));
    const minY = Math.min(...flatPoints.map((point) => point[1]));
    const maxY = Math.max(...flatPoints.map((point) => point[1]));
    const spanX = Math.max(maxX - minX, 1e-6);
    const spanY = Math.max(maxY - minY, 1e-6);
    const modelScale = zoom * 0.88 * Math.min(width / spanX, height / spanY);
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    projected.sort((left, right) => left.depth - right.depth);
    for (const triangle of projected) {
      context.beginPath();
      triangle.points.forEach((point, index) => {
        const x = width / 2 + (point[0] - centerX) * modelScale;
        const y = height / 2 - (point[1] - centerY) * modelScale;
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
      });
      context.closePath();
      const r = Math.round(26 * triangle.shade);
      const g = Math.round(108 * triangle.shade + 55);
      const b = Math.round(170 * triangle.shade + 48);
      context.fillStyle = `rgb(${r}, ${g}, ${b})`;
      context.fill();
      context.strokeStyle = 'rgba(15, 76, 129, 0.25)';
      context.stroke();
    }
  }

  const pointerDown = (event) => { dragging = true; previous = event; canvas.setPointerCapture(event.pointerId); };
  const pointerMove = (event) => {
    if (!dragging || !previous) return;
    yaw += (event.clientX - previous.clientX) * 0.012;
    pitch = Math.max(-1.45, Math.min(1.45, pitch + (event.clientY - previous.clientY) * 0.012));
    previous = event; draw();
  };
  const pointerUp = () => { dragging = false; previous = null; };
  const wheel = (event) => {
    // 鼠标滚轮围绕模型中心缩放；限制倍率以防模型缩得过小或放得过大。
    event.preventDefault();
    zoom = Math.max(0.35, Math.min(4, zoom * (event.deltaY < 0 ? 1.12 : 0.89)));
    draw();
  };
  canvas.addEventListener('pointerdown', pointerDown);
  canvas.addEventListener('pointermove', pointerMove);
  canvas.addEventListener('pointerup', pointerUp);
  canvas.addEventListener('pointercancel', pointerUp);
  canvas.addEventListener('wheel', wheel, { passive: false });
  draw();
  return () => {
    canvas.removeEventListener('pointerdown', pointerDown);
    canvas.removeEventListener('pointermove', pointerMove);
    canvas.removeEventListener('pointerup', pointerUp);
    canvas.removeEventListener('pointercancel', pointerUp);
    canvas.removeEventListener('wheel', wheel);
  };
}
""",
)


def render_step_file(source: Path, *, key: str, height: int = 420) -> None:
    """将指定的真实 STEP 文件显示为可拖动旋转的三维视图。"""
    if not source.is_file():
        st.warning("该检索结果的 STEP 源文件不存在，无法显示三维模型。")
        return
    payload = step_mesh_payload(str(source.resolve()))
    _VIEWER(data=payload, key=key, height=height)
    st.caption(f"真实 STEP 网格：{payload['triangle_count']} 个三角面；拖动模型可旋转查看。")


def render_step_model(record: dict[str, Any], *, key: str, height: int = 420) -> None:
    """将目录记录对应的真实 STEP 模型显示为可拖动旋转的三维视图。"""
    source_file = record.get("source_file")
    if not source_file and record.get("part_id"):
        # 兼容热更新前已存在的检索结果：旧结果对象可能没有 source_file，
        # 此时依据稳定的 part_id 从最新目录补回真实 STEP 路径。
        from machining_unified.cad.retrieval import load_cad_catalog

        source_file = next(
            (item.get("source_file") for item in load_cad_catalog() if item.get("part_id") == record["part_id"]),
            None,
        )
    render_step_file((PROJECT_ROOT / str(source_file or "")).resolve(), key=key, height=height)
