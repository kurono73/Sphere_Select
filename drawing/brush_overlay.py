"""GPU-only brush overlay.  No scene objects are created."""

from math import cos, pi, sin

import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from array import array

_highlight_shader = None
_sphere_shader = None


def highlight_shader():
    """Small clip-space depth bias: retain occlusion without coplanar z fighting."""
    global _highlight_shader
    if _highlight_shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', 'mvp')
        info.push_constant('VEC4', 'color')
        info.vertex_in(0, 'VEC3', 'pos')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source('''
            void main() {
                gl_Position = mvp * vec4(pos, 1.0);
                gl_Position.z -= 0.00002 * gl_Position.w;
            }
        ''')
        info.fragment_source('void main() { fragColor = color; }')
        _highlight_shader = gpu.shader.create_from_info(info)
    return _highlight_shader


def _circle(center, axis_u, axis_v, radius, segments=48):
    return [center + radius * (axis_u * cos(2.0 * pi * i / segments) + axis_v * sin(2.0 * pi * i / segments))
            for i in range(segments + 1)]


def _basis(normal):
    normal = normal.normalized()
    helper = Vector((0.0, 0.0, 1.0)) if abs(normal.z) < 0.9 else Vector((0.0, 1.0, 0.0))
    u = normal.cross(helper).normalized()
    return u, normal.cross(u).normalized()


def _draw_line(shader, points, color, width=1.0):
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": points})
    shader.bind()
    shader.uniform_float("color", color)
    gpu.state.line_width_set(width)
    batch.draw(shader)


def _draw_fresnel_shell(center, radius, view_direction, rings=12, segments=32):
    # Analytic sphere intersection avoids low-poly silhouette gaps, back/front
    # triangle overlaps and near-plane clipping when the camera enters the sphere.
    global _sphere_shader
    if _sphere_shader is None:
        info = gpu.types.GPUShaderCreateInfo()
        info.typedef_source('struct SphereUniforms { mat4 inverseVP; mat4 vp; vec4 centerRadius; };')
        info.uniform_buf(0, 'SphereUniforms', 'sphere')
        interface = gpu.types.GPUStageInterfaceInfo('sphere_uv')
        interface.smooth('VEC2', 'ndc')
        info.vertex_out(interface)
        info.vertex_in(0, 'VEC2', 'pos')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.depth_write('ANY')
        info.vertex_source('void main(){ndc=pos; gl_Position=vec4(pos,0.0,1.0);}')
        info.fragment_source('''
        void main(){
            vec3 center=sphere.centerRadius.xyz;
            float radius=sphere.centerRadius.w;
            vec4 a=sphere.inverseVP*vec4(ndc,-1.0,1.0);
            vec4 b=sphere.inverseVP*vec4(ndc,1.0,1.0);
            vec3 origin=a.xyz/a.w;
            vec3 direction=normalize(b.xyz/b.w-origin);
            vec3 offset=origin-center;
            float along=dot(offset,direction);
            vec3 perpendicular=offset-along*direction;
            float discriminant=radius*radius-dot(perpendicular,perpendicular);
            if(discriminant<0.0) discard;
            float delta=sqrt(discriminant);
            float t=-along-delta;
            if(t<0.0) t=-along+delta;
            if(t<0.0) discard;
            vec3 hit=origin+t*direction;
            vec4 clip=sphere.vp*vec4(hit,1.0);
            float depth=clip.z/clip.w*0.5+0.5;
            if(depth<0.0 || depth>1.0) discard;
            gl_FragDepth=depth;
            float rim=1.0-abs(dot(normalize(hit-center),direction));
            fragColor=vec4(0.60,0.88,1.0,0.015+0.20*rim*rim*rim);
        }''')
        _sphere_shader = gpu.shader.create_from_info(info)
    shader = _sphere_shader
    vp = gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix()
    shader.bind()
    packed = array('f', [v for matrix in (vp.inverted_safe(), vp)
                         for row in matrix.transposed() for v in row] + list(center) + [radius])
    uniform = gpu.types.GPUUniformBuf(packed)
    shader.uniform_block('sphere', uniform)
    batch_for_shader(shader, 'TRIS', {'pos': [(-1,-1),(3,-1),(-1,3)]}).draw(shader)


def _draw_highlight(shader, geometry, color=None):
    if not geometry:
        return
    if isinstance(geometry, dict):
        rgb = geometry.get('color', color or (1.0, 0.04, 0.03))
        shader = highlight_shader()
        shader.bind()
        shader.uniform_float('mvp', gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
        shader.uniform_float('color', (*rgb, 0.22))
        for surface in geometry['objects']:
            if len(surface['triangles']) == 0:
                continue
            if surface['batch'] is None:
                surface['batch'] = batch_for_shader(shader, 'TRIS', {'pos': surface['positions']}, indices=surface['triangles'])
            surface['batch'].draw(shader)
        markers = geometry.get('markers', [])
        if markers:
            # Origin markers must remain identifiable even when inside a dense object.
            gpu.state.depth_test_set('NONE')
            shader.uniform_float('color', (*rgb, 0.95))
            gpu.state.point_size_set(9.0)
            batch_for_shader(shader, 'POINTS', {'pos': markers}).draw(shader)
        points = geometry.get('point_preview', ())
        if len(points):
            # Native point clouds render spheres: their centers lie behind their
            # own depth. X-ray candidate dots also show the full selection volume.
            gpu.state.depth_test_set('NONE')
            shader.uniform_float('color', (*rgb, 0.65))
            gpu.state.point_size_set(5.0)
            batch_for_shader(shader, 'POINTS', {'pos': points}).draw(shader)
        points = geometry.get('mesh_points', ())
        if len(points):
            gpu.state.depth_test_set('LESS_EQUAL')
            shader.uniform_float('color', (*rgb, 0.7))
            gpu.state.point_size_set(3.0)
            batch_for_shader(shader, 'POINTS', {'pos': points}).draw(shader)
        return
    points, lines, triangles = geometry
    rgb = color or (1.0, 0.06, 0.02)
    shader = highlight_shader()
    shader.bind()
    shader.uniform_float('mvp', gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    if triangles:
        batch = batch_for_shader(shader, 'TRIS', {"pos": triangles})
        shader.bind()
        shader.uniform_float("color", (*rgb, 0.20))
        batch.draw(shader)
    if lines:
        batch = batch_for_shader(shader, 'LINES', {"pos": lines})
        shader.bind()
        shader.uniform_float("color", (*rgb, 0.92))
        gpu.state.line_width_set(1.5)
        batch.draw(shader)
    if not points:
        return
    batch = batch_for_shader(shader, 'POINTS', {"pos": points})
    shader.bind()
    shader.uniform_float("color", (*rgb, 0.90))
    gpu.state.point_size_set(4.0)
    batch.draw(shader)


def draw_selection_trail(geometry):
    """Draw the staged selection even when the brush currently misses."""
    mask, depth, blend = gpu.state.depth_mask_get(), gpu.state.depth_test_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_mask_set(False)
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.blend_set('ALPHA')
        _draw_highlight(None, geometry)
    finally:
        gpu.state.point_size_set(1.0)
        gpu.state.depth_mask_set(mask)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def draw_brush(center, normal, radius, view_direction, highlight_geometry, highlight_color=None):
    """Draw the contact ring plus an unambiguous world-space wire sphere."""
    if center is None or normal is None or radius <= 0.0:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    previous_depth_mask = gpu.state.depth_mask_get()
    previous_depth_test = gpu.state.depth_test_get()
    previous_blend = gpu.state.blend_get()
    # Overlays must never occlude the mesh or their own subsequent highlight.
    gpu.state.depth_mask_set(False)
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    try:
        if view_direction is not None:
            _draw_fresnel_shell(center, radius, view_direction)
        ring_u, ring_v = _basis(normal)
        _draw_line(shader, _circle(center, ring_u, ring_v, radius), (0.15, 0.8, 1.0, 0.95), 2.0)
        # Three perpendicular great circles communicate the finite spherical volume.
        for u, v in ((Vector((1, 0, 0)), Vector((0, 1, 0))),
                     (Vector((1, 0, 0)), Vector((0, 0, 1))),
                     (Vector((0, 1, 0)), Vector((0, 0, 1)))):
            _draw_line(shader, _circle(center, u, v, radius, 32), (0.15, 0.8, 1.0, 0.38), 1.0)
        _draw_highlight(shader, highlight_geometry, highlight_color)
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.point_size_set(1.0)
        gpu.state.depth_mask_set(previous_depth_mask)
        gpu.state.depth_test_set(previous_depth_test)
        gpu.state.blend_set(previous_blend)


def draw_status_cursor(x, y, radius, miss, pixel_radius=12.0):
    """Screen-space activity indicator; never invent a selectable depth on a miss."""
    import blf
    blend, depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        color = (0.55, 0.85, 1.0, 0.9)
        for start in range(0, 48, 8):
            points = [(x+pixel_radius*cos(2*pi*i/48), y+pixel_radius*sin(2*pi*i/48)) for i in range(start,start+5)]
            _draw_line(shader, points, color, 1.5)
        if miss:
            blf.position(0, x+19, y+12, 0)
            blf.size(0, 12)
            blf.color(0, *color)
            blf.draw(0, f'Sphere Select  |  R {radius:.4g}  |  No surface')
    finally:
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set(blend)
        gpu.state.depth_test_set(depth)
