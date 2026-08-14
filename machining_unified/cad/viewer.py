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


# 每帧都要对全部三角面做旋转、投影和深度排序，因此这是自动旋转流畅度的主要变量。
# 190~260px 的预览框分辨不出三千个面与一千八百个面的差别，取后者换取帧率。
# 需要更高保真时提高此值，但要同时评估一屏多画布下的总开销。
MAX_TRIANGLES = 1_800
# 预览高度。结果页会同时出现多个画布，过高会把证据信息挤出首屏；
# 缩小后仍足以辨认零件形态，也降低了自动旋转时的每帧绘制量。
PREVIEW_HEIGHT = 260


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
            /* 尺寸由 JS 按宿主容器显式写入 style，这里只留首帧前的兜底值。 */
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
  // 组件的 parentElement 是 ShadowRoot（没有 getBoundingClientRect），
  // 其 .host 才是 Streamlit 按 height 参数给定确定高度的宿主元素。
  const hostElement = parentElement.host || canvas.parentElement || canvas;

  // 缩放不逐帧重算——那会让模型边转边被反复缩放，呈现呼吸式抖动。
  // 但也不能简单套包围球：卡片画布是宽扁形（约 522×190），按球半径取短边会把
  // 模型塞进一个 190px 的圆里，左右大片留白。改为分别约束水平与垂直方向。
  //
  // 水平半宽在自动旋转下是不变量：绕 Y 轴转 yaw 时，投影横坐标的最大绝对值
  // 恒等于顶点在 XZ 平面内的最大半径。
  // 垂直半高只随 pitch 变化，对每个顶点取整圈 yaw 的上确界后再取最大值，
  // 即 |y|·|cos p| + r_xz·|sin p|，可在 pitch 改变时以 O(n) 重算。
  let fitHalfWidth = 1e-6;
  const radialXZ = [];
  const absY = [];
  for (const triangle of data.triangles) {
    for (let i = 0; i < 9; i += 3) {
      const r = Math.hypot(triangle[i], triangle[i + 2]);
      radialXZ.push(r);
      absY.push(Math.abs(triangle[i + 1]));
      if (r > fitHalfWidth) fitHalfWidth = r;
    }
  }

  let fitHalfHeight = 1e-6;
  function refreshVerticalFit() {
    const cp = Math.abs(Math.cos(pitch));
    const sp = Math.abs(Math.sin(pitch));
    let half = 1e-6;
    for (let i = 0; i < absY.length; i++) {
      const extent = absY[i] * cp + radialXZ[i] * sp;
      if (extent > half) half = extent;
    }
    fitHalfHeight = half;
  }
  let yaw = 0.65;
  let pitch = -0.45;
  let zoom = 1;
  let dragging = false;
  let previous = null;
  // 必须在 pitch 声明之后调用：refreshVerticalFit 读取 pitch，
  // 提前调用会命中 let 的暂时性死区并抛 ReferenceError。
  refreshVerticalFit();

  function rotate(x, y, z) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x1 = x * cy - z * sy;
    const z1 = x * sy + z * cy;
    return [x1, y * cp - z1 * sp, y * sp + z1 * cp];
  }

  function draw() {
    // 按宿主容器（Streamlit 用 height 参数给定的确定高度）显式设定画布像素尺寸。
    // 不能量 canvas 自身：它的 CSS height:100% 在这里解析不到确定的父高度，
    // 会退回 height 属性值，而该属性又由上一次 draw 写入——形成自我放大的反馈环，
    // 实测会让画布变成容器的两倍高并被 overflow:hidden 裁掉下半部分。
    const host = hostElement.getBoundingClientRect();
    const width = Math.max(1, host.width);
    const height = Math.max(1, host.height);
    const scale = Math.min(2, window.devicePixelRatio || 1);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    canvas.width = Math.max(1, Math.floor(width * scale));
    canvas.height = Math.max(1, Math.floor(height * scale));
    context.setTransform(scale, 0, 0, scale, 0, 0);
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
    // 水平、垂直分别取能容下整圈旋转的尺度，再取较小者：既填满卡片，又不会在任何角度被裁。
    const modelScale = zoom * 0.92 * Math.min(width / (2 * fitHalfWidth), height / (2 * fitHalfHeight));
    projected.sort((left, right) => left.depth - right.depth);
    for (const triangle of projected) {
      context.beginPath();
      triangle.points.forEach((point, index) => {
        const x = width / 2 + point[0] * modelScale;
        const y = height / 2 - point[1] * modelScale;
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

  // 自动旋转。一次检索可能同时渲染近十个画布，每个最多三千个三角面，
  // 因此有两道限流：只有滚动进视口的画布才动，且重绘节流到约 25fps。
  // 用户一旦拖动就永久接管，不再自动转，避免手动对准的角度被转走。
  // 跟随显示器刷新率（约 60fps）。此前节流到 25fps 是为了压住多画布的开销，
  // 但改用固定缩放后每帧省掉了一次全顶点极值遍历，且视口外的画布本就不绘制，
  // 因此可以放开。仍保留一个下限间隔，避免高刷新率屏幕上无谓地烧 CPU。
  const FRAME_INTERVAL = 16;
  const YAW_PER_SECOND = 0.35;
  let autoRotate = true;
  let visible = false;
  let frame = null;
  let lastFrameAt = 0;

  function tick(now) {
    if (!autoRotate || !visible) { frame = null; return; }
    if (now - lastFrameAt >= FRAME_INTERVAL) {
      yaw += YAW_PER_SECOND * ((now - lastFrameAt) / 1000);
      lastFrameAt = now;
      draw();
    }
    frame = requestAnimationFrame(tick);
  }

  function startAuto() {
    if (frame === null && autoRotate && visible) {
      lastFrameAt = performance.now();
      frame = requestAnimationFrame(tick);
    }
  }

  function stopAuto() {
    if (frame !== null) { cancelAnimationFrame(frame); frame = null; }
  }

  const observer = new IntersectionObserver((entries) => {
    visible = entries.some((entry) => entry.isIntersecting);
    if (visible) startAuto(); else stopAuto();
  }, { threshold: 0.1 });
  observer.observe(canvas);

  const pointerDown = (event) => {
    // 交给用户控制：停掉自动旋转并释放动画帧。
    autoRotate = false;
    stopAuto();
    dragging = true; previous = event; canvas.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event) => {
    if (!dragging || !previous) return;
    yaw += (event.clientX - previous.clientX) * 0.012;
    const nextPitch = Math.max(-1.45, Math.min(1.45, pitch + (event.clientY - previous.clientY) * 0.012));
    if (nextPitch !== pitch) { pitch = nextPitch; refreshVerticalFit(); }
    previous = event; draw();
  };
  const pointerUp = () => { dragging = false; previous = null; };
  const wheel = (event) => {
    // 鼠标滚轮围绕模型中心缩放；限制倍率以防模型缩得过小或放得过大。
    event.preventDefault();
    autoRotate = false;
    stopAuto();
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
    // 组件卸载时必须同时停掉动画帧和观察器，否则 Streamlit 重跑后
    // 旧画布的 rAF 循环会继续持有已废弃的 canvas 与网格数据。
    stopAuto();
    observer.disconnect();
    canvas.removeEventListener('pointerdown', pointerDown);
    canvas.removeEventListener('pointermove', pointerMove);
    canvas.removeEventListener('pointerup', pointerUp);
    canvas.removeEventListener('pointercancel', pointerUp);
    canvas.removeEventListener('wheel', wheel);
  };
}
""",
)


def render_step_payload(payload: dict[str, Any], *, key: str, height: int = PREVIEW_HEIGHT) -> None:
    """渲染已提取好的 STEP 网格。

    上传的查询模型使用临时文件，检索结束后会被删除；保留网格数据本身，
    才能在后续的页面重跑中继续显示三维预览。
    """
    if not payload or not payload.get("triangles"):
        st.warning("没有可显示的 STEP 三角网格。")
        return
    _VIEWER(data=payload, key=key, height=height)
    # 结果卡片排成两列，说明文字必须短，否则在半宽列里会折成三行挤掉证据信息。
    st.caption(f"{payload['triangle_count']} 个三角面 · 自动旋转 · 可拖动/缩放")


def render_step_file(source: Path, *, key: str, height: int = PREVIEW_HEIGHT) -> None:
    """将指定的真实 STEP 文件显示为自动旋转、可拖动接管的三维视图。"""
    if not source.is_file():
        st.warning("该检索结果的 STEP 源文件不存在，无法显示三维模型。")
        return
    render_step_payload(step_mesh_payload(str(source.resolve())), key=key, height=height)


def render_step_model(record: dict[str, Any], *, key: str, height: int = PREVIEW_HEIGHT) -> None:
    """将目录记录对应的真实 STEP 模型显示为自动旋转、可拖动接管的三维视图。"""
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
