"""Interactive, surface-snapped 3D sphere selection."""

from math import ceil, isfinite

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix
from mathutils.geometry import tessellate_polygon

from ..drawing.brush_overlay import draw_brush, draw_selection_trail
from ..spatial.mesh_bvh import build_edit_mesh_bvh, raycast_local
from ..spatial.vertex_kdtree import build_visible_vertex_tree


# Deliberately internal in v1: tuned for scan cleanup rather than exposed as UI clutter.
SURFACE_JUMP_THRESHOLD = 2.0
POSITION_SMOOTHING = 0.45
STROKE_SPACING_FRACTION = 0.25
MAX_HIGHLIGHT_POINTS = 3000
MESH_PREVIEW_POINT_THRESHOLD = 1500
MESH_PREVIEW_TRIANGLE_LIMIT = 60000
WHEEL_RADIUS_FACTOR = 1.1


class VIEW3D_OT_sphere_select(bpy.types.Operator):
    """Select with a finite, surface-snapped 3D sphere"""

    bl_idname = "view3d.sphere_select"
    bl_label = "Sphere Select"
    bl_options = {'REGISTER', 'UNDO'}
    wait_for_input: bpy.props.BoolProperty(name='Wait for Input', default=False, options={'SKIP_SAVE'})
    continuous: bpy.props.BoolProperty(name='Continuous Session', default=False, options={'SKIP_SAVE'})
    operation: bpy.props.EnumProperty(name="Selection Operation", items=(
        ('TOOL', 'Tool Setting', 'Use the selected toolbar mode'),
        ('SET', 'Set', 'Replace selection'), ('ADD', 'Extend', 'Add selection'),
        ('SUB', 'Subtract', 'Remove selection')), default='TOOL')

    _draw_handle = None
    _bm = None
    _bvh = None
    _kdtree = None
    _object = None
    _region = None
    _region_data = None
    _center_world = None
    _normal_world = None
    _previous_raw_world = None
    _locked_target_world = None
    _lock_frames = 0
    _stroke_center_world = None
    _dragging = False
    _selection_operation = 'ADD'
    _stroke_select_mode = None
    _preview_geometry = None
    _view_direction_world = None
    _topology_signature = None
    _point_mode = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.area and context.area.type == 'VIEW_3D' and (context.mode == 'OBJECT' or
            (obj and obj.type in {'MESH','POINTCLOUD'} and obj.mode == 'EDIT'))

    def _settings(self, context):
        return context.window_manager.sphere_select_settings

    def _topology_is_current(self):
        try:
            if self._object_mode:
                return True
            if self._point_mode:
                return len(self._object.data.points) == self._points.count
            return (self._bm and self._bm.is_valid and
                    len(self._bm.verts) == self._topology_signature[0] and
                    len(self._bm.faces) == self._topology_signature[1])
        except ReferenceError:
            return False

    def _build_acceleration(self, context):
        if context.mode == 'OBJECT':
            from ..spatial.object_mesh import SceneIndex, signature
            self._scene_index = SceneIndex(context)
            self._scene_signature = signature(context)
            self._matrix_world = Matrix.Identity(4)
            return len(self._scene_index.entries)
        if context.mode == 'EDIT_POINTCLOUD':
            from ..spatial.point_cloud import PointIndex
            self._point_mode = True
            self._matrix_world = Matrix.Identity(4)
            self._points = PointIndex(self._object.data, self._object.matrix_world)
            return self._points.count
        self._object_mode = False
        self._matrix_world = self._object.matrix_world.copy()
        self._bm = bmesh.from_edit_mesh(self._object.data)
        self._bm.verts.ensure_lookup_table()
        self._bm.faces.ensure_lookup_table()
        self._topology_signature = (len(self._bm.verts), len(self._bm.faces))
        self._bvh = build_edit_mesh_bvh(self._bm)
        self._kdtree, count = build_visible_vertex_tree(self._bm)
        self._face_triangles = {}
        return count

    def _local_ray(self, event):
        mouse = ((event.mouse_x - self._region.x, event.mouse_y - self._region.y)
                 if hasattr(event, 'mouse_x') else (event.mouse_region_x, event.mouse_region_y))
        origin_world = view3d_utils.region_2d_to_origin_3d(self._region, self._region_data, mouse)
        direction_world = view3d_utils.region_2d_to_vector_3d(self._region, self._region_data, mouse)
        inverse = self._matrix_world.inverted_safe()
        origin_local = inverse @ origin_world
        direction_local = (inverse.to_3x3() @ direction_world).normalized()
        return origin_local, direction_local, direction_world

    def _update_hit(self, context, event):
        self._view_direction_world = None
        if not self._object_mode and not self._point_mode and not self._bvh:
            self._center_world = None
            return False
        origin, direction, direction_world = self._local_ray(event)
        self._view_direction_world = -direction_world
        if self._object_mode:
            result = self._scene_index.raycast(origin, direction, self._settings(context).radius)
        elif self._point_mode:
            result = self._points.raycast(origin, direction)
        else:
            result = raycast_local(self._bvh, origin, direction)
            while result is not None and self._bm.faces[result[2]].hide:
                origin = result[0] + direction * 1e-5
                result = raycast_local(self._bvh, origin, direction)
        if result is None or not all(isfinite(v) for v in result[0]):
            # Break the old anchor as well as the drawing: reacquisition must not
            # interpolate from a distant surface or paint across empty space.
            self._center_world = None
            self._previous_raw_world = None
            self._locked_target_world = None
            self._lock_frames = 0
            self._stroke_center_world = None
            self._preview_geometry = None
            return False
        local_hit, local_normal, _face_index, _distance = result
        matrix = self._matrix_world
        target_world = matrix @ local_hit
        settings = self._settings(context)
        radius = settings.radius
        if (self._dragging and settings.surface_lock and self._previous_raw_world is not None
                and (target_world - self._previous_raw_world).length > radius * SURFACE_JUMP_THRESHOLD):
            # During a stroke Surface Lock must have an unmistakable effect: keep
            # painting the current surface rather than crossing a scan hole.
            return self._center_world is not None
        self._locked_target_world = None
        self._lock_frames = 0
        self._previous_raw_world = target_world
        if not self._dragging or self._center_world is None:
            self._center_world = target_world
        else:
            self._center_world = self._center_world.lerp(target_world, POSITION_SMOOTHING)
        self._normal_world = (matrix.inverted_safe().transposed().to_3x3() @ local_normal).normalized()
        if not self._dragging:
            self._preview_geometry = self._preview_geometry_for_mode(self._center_world, radius)
        return True

    def _vertices_in_sphere(self, center_world, radius, limit=None):
        if self._kdtree is None:
            return []
        key = (tuple(center_world), radius, tuple(v for row in self._matrix_world for v in row))
        if getattr(self, '_query_key', None) == key:
            return self._query_result if limit is None else self._query_result[:limit]
        # KDTree is local. Search conservatively, then perform the exact world-space sphere test.
        scale = self._matrix_world.to_scale()
        minimum_scale = min(abs(scale.x), abs(scale.y), abs(scale.z))
        if minimum_scale <= 1e-12:
            return []
        inverse = self._matrix_world.inverted_safe()
        local_center = inverse @ center_world
        # Frobenius norm bounds arbitrary transforms, including parent-induced shear.
        linear = self._matrix_world.to_3x3()
        gram = linear.transposed() @ linear
        uniform = (max(abs(gram[i][j]) for i in range(3) for j in range(3) if i != j) < 1e-8 * minimum_scale**2
                   and max(abs(gram[i][i]-gram[0][0]) for i in range(3)) < 1e-8 * minimum_scale**2)
        local_radius = radius / abs(scale.x) if uniform else radius * sum(v*v for row in inverse.to_3x3() for v in row)**0.5
        found = []
        for _co, index, _distance in self._kdtree.find_range(local_center, local_radius):
            vert = self._bm.verts[index]
            if vert.hide:
                continue
            if uniform or (self._matrix_world @ vert.co - center_world).length_squared <= radius*radius:
                found.append(vert)
        self._query_key, self._query_result = key, found
        return found if limit is None else found[:limit]

    def _preview_geometry_for_mode(self, center_world, radius):
        key = (tuple(center_world), radius, tuple(bpy.context.tool_settings.mesh_select_mode))
        if getattr(self, '_preview_cache_key', None) != key:
            self._preview_cache_geometry = self._build_preview_geometry(center_world, radius)
            self._preview_cache_key = key
        return self._preview_cache_geometry

    def _build_preview_geometry(self, center_world, radius):
        """Return the same component hierarchy that the active select mode will affect."""
        if self._object_mode:
            return self._scene_index.preview(center_world, radius)
        if self._point_mode:
            ids = self._points.query(center_world,radius)
            stride = max(1, ceil(len(ids)/MAX_HIGHLIGHT_POINTS))
            return {'objects': [], 'point_preview': self._points.positions[ids[::stride]]}
        queried = self._vertices_in_sphere(center_world, radius)
        vertices = set(queried)
        mode = (False, False, True) if self._object_mode else tuple(bpy.context.tool_settings.mesh_select_mode)
        if mode[0]:
            edges = {edge for vert in vertices for edge in vert.link_edges
                     if not edge.hide and all(v in vertices for v in edge.verts)}
            faces = {face for vert in vertices for face in vert.link_faces
                     if not face.hide and all(v in vertices for v in face.verts)}
        elif mode[1]:
            edges = {edge for vert in vertices for edge in vert.link_edges if not edge.hide}
            vertices = {vert for edge in edges for vert in edge.verts}
            faces = {face for edge in edges for face in edge.link_faces
                     if not face.hide and all(link_edge in edges for link_edge in face.edges)}
        else:
            faces = {face for vert in vertices for face in vert.link_faces if not face.hide}
            edges = {edge for face in faces for edge in face.edges}
            vertices = {vert for face in faces for vert in face.verts}
        matrix = self._matrix_world
        if len(queried) > MESH_PREVIEW_POINT_THRESHOLD:
            # A sparse vertex sample follows KDTree traversal order and looks
            # like broken selection.  Keep the same component propagation, but
            # draw a bounded, deterministic face subset instead.
            triangles = self._triangles_for_faces(faces, MESH_PREVIEW_TRIANGLE_LIMIT)
            if triangles:
                return [], [], triangles
            ordered = sorted(vertices, key=lambda vert: vert.index)
            stride = max(1, ceil(len(ordered) / MAX_HIGHLIGHT_POINTS))
            return [matrix @ vert.co for vert in ordered[::stride]], [], []
        points = [matrix @ vert.co for vert in vertices]
        lines = [coordinate for edge in edges for coordinate in (matrix @ edge.verts[0].co, matrix @ edge.verts[1].co)]
        triangles = self._triangles_for_faces(faces)
        return points, lines, triangles

    def _triangles_for_faces(self, faces, limit=None):
        """Build a stable, capped face overlay without random KDTree sampling."""
        if not faces:
            return []
        ordered = sorted(faces, key=lambda face: face.index)
        triangle_count = sum(max(1, len(face.verts) - 2) for face in ordered)
        stride = max(1, ceil(triangle_count / limit)) if limit else 1
        triangles = []
        for ordinal, face in enumerate(ordered):
            # Only tessellate the brush footprint, never the full scan.
            if ordinal % stride:
                continue
            # Only tessellate the small brush footprint, not the whole scan on entry.
            cached = self._face_triangles.get(face.index)
            if cached is None:
                coords = [v.co for v in face.verts]
                cached = [self._matrix_world @ (coords[co] if isinstance(co, int) else co)
                          for triangle in tessellate_polygon([coords]) for co in triangle]
                if len(self._face_triangles) >= 24000:
                    self._face_triangles.clear()
                self._face_triangles[face.index] = cached
            triangles.extend(cached)
            if limit and len(triangles) >= limit * 3:
                break
        return triangles

    def _clear_selection(self):
        if self._point_mode:
            self._points.clear()
            return
        if self._object_mode:
            for obj in bpy.context.selected_objects:
                if not obj.hide_select and obj.visible_get():
                    obj.select_set(False)
            return
        # SET scans once per stroke only.  It intentionally clears every component
        # domain so changing selection modes cannot leave stale visual selections.
        # Fallback for direct internal calls; interactive SET uses the native operator.
        for domain in (self._bm.faces,self._bm.edges,self._bm.verts):
            for element in domain:
                if element.select and not element.hide:
                    element.select = False

    def _commit_mesh_selection(self):
        """Flush one completed provisional mesh stroke to Blender's viewport."""
        self._bm.select_flush_mode()
        from ..tools.preview import selection_update
        selection_update(self._object)
        bmesh.update_edit_mesh(self._object.data, loop_triangles=False, destructive=False)

    def _paint_segment(self, context):
        """Accumulate temporary candidates without changing Blender selection."""
        if self._center_world is None:
            return
        radius = self._settings(context).radius
        current = self._center_world.copy()
        if self._stroke_center_world is None:
            samples = [current]
        else:
            distance = (current - self._stroke_center_world).length
            sample_count = max(1, ceil(distance / max(radius * STROKE_SPACING_FRACTION, 1e-9)))
            samples = [self._stroke_center_world.lerp(current, i / sample_count)
                       for i in range(1, sample_count + 1)]
        for sample in samples:
            self._stage_at(sample, radius)
        self._stroke_center_world = current

    def _stage_at(self, center, radius):
        if self._object_mode:
            for entry in self._scene_index.candidates(center, radius):
                if entry.obj in self._staged:
                    continue
                self._staged.add(entry.obj)
                if entry.surface is not None:
                    self._trail['objects'].append(entry.surface)
                else:
                    self._trail['markers'].append(entry.origin)
            return
        if self._point_mode:
            ids = set(map(int, self._points.query(center, radius))) - self._staged
            self._staged.update(ids)
            room = max(0, MAX_HIGHLIGHT_POINTS-len(self._trail['point_preview']))
            if room and ids:
                ordered = sorted(ids)
                stride = max(1, ceil(len(ordered)/room))
                self._trail['point_preview'].extend(self._points.positions[ordered[::stride]])
            return
        vertices = self._vertices_in_sphere(center, radius)
        mode = self._stroke_select_mode
        if mode[0]:
            added = set(vertices)-self._staged
        elif mode[1]:
            added = {e for v in vertices for e in v.link_edges if not e.hide}-self._staged
        else:
            added = {f for v in vertices for f in v.link_faces if not f.hide}-self._staged
        if not added:
            return
        self._staged.update(added)
        if mode[0]:
            affected = added
            faces = {f for v in added for f in v.link_faces
                     if not f.hide and all(v in self._staged for v in f.verts)}
        elif mode[1]:
            affected = {v for e in added for v in e.verts}
            faces = {f for e in added for f in e.link_faces
                     if not f.hide and all(e in self._staged for e in f.edges)}
        else:
            faces = added
            affected = {v for f in added for v in f.verts}
        coords = []
        for face in faces-self._trail_faces:
            self._trail_faces.add(face)
            vertices_local = [v.co for v in face.verts]
            if len(vertices_local) == 3:
                coords.extend(self._matrix_world @ v for v in vertices_local)
                continue
            coords.extend(self._matrix_world @ (vertices_local[i] if isinstance(i,int) else i)
                          for triangle in tessellate_polygon([vertices_local]) for i in triangle)
        if coords:
            self._trail['objects'].append({'positions':coords,
                'triangles':[(i,i+1,i+2) for i in range(0,len(coords),3)], 'batch':None})
        # Loose components still need feedback where no complete face is covered.
        room = max(0, MAX_HIGHLIGHT_POINTS-len(self._trail['mesh_points']))
        if room:
            fresh = list(affected-self._trail_vertices)
            stride = max(1,ceil(len(fresh)/room))
            self._trail['mesh_points'].extend(self._matrix_world @ v.co for v in fresh[::stride])
        self._trail_vertices.update(affected)

    def _commit_stroke(self, context):
        if not self._dragging:
            return
        from ..tools.preview import selection_update
        if self._selection_operation == 'SET':
            if not self._object_mode and not self._point_mode:
                selection_update(self._object)
                bpy.ops.mesh.select_all(action='DESELECT')
            else:
                self._clear_selection()
        select = self._selection_operation != 'SUB'
        if self._object_mode:
            for obj in self._staged:
                obj.select_set(select)
        elif self._point_mode:
            self._points.apply_ids(sorted(self._staged), select)
            selection_update(self._object)
            self._object.data.update_tag()
        else:
            for element in self._staged:
                if element.select != select:
                    element.select_set(select)
            self._commit_mesh_selection()
        self._dragging = False
        self._staged.clear()
        self._trail_faces.clear()
        self._trail_vertices.clear()
        self._trail = None

    def _draw_callback(self):
        if bpy.context.region != self._region:
            return
        draw_brush(self._center_world, self._normal_world, self._settings(bpy.context).radius,
                   self._view_direction_world, None if self._dragging else self._preview_geometry,
                   (0.08,1.0,0.22) if self.operation == 'SUB' else None)
        if self._dragging:
            draw_selection_trail(self._trail)

    def _begin_stroke(self, context, event):
        settings = self._settings(context)
        self._selection_operation = settings.selection_operation if self.operation == 'TOOL' else self.operation
        self._stroke_select_mode = tuple(context.tool_settings.mesh_select_mode)
        self._staged = set()
        self._trail_faces = set()
        self._trail_vertices = set()
        self._trail = {'objects':[], 'markers':[], 'mesh_points':[], 'point_preview':[]}
        if self._selection_operation == 'SUB':
            self._trail['color'] = (0.08,1.0,0.22)
        self._dragging = True
        self._stroke_center_world = None

    def _remove_draw_handler(self):
        if self._draw_handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._draw_handle, 'WINDOW')
            except (ReferenceError, ValueError):
                pass
            self._draw_handle = None

    def _finish(self, context, cancelled=False):
        from ..tools import preview
        if not cancelled and getattr(self, '_dragging', False):
            self._commit_stroke(context)
        self._trail = None
        if hasattr(self, '_staged'):
            self._staged.clear()
            getattr(self, '_trail_faces', set()).clear()
            getattr(self, '_trail_vertices', set()).clear()
        if context.area:
            preview.busy.discard(context.area.as_pointer())
            preview.active.pop(context.area.as_pointer(), None)
        preview.drop_cache_if_inactive(context)
        self._remove_draw_handler()
        if context.area:
            context.area.tag_redraw()
        self._dragging = False
        return {'CANCELLED'} if cancelled else {'FINISHED'}

    def invoke(self, context, event):
        from ..tools import preview
        self._release_key = event.type
        self._center_world = None
        self._normal_world = None
        self._previous_raw_world = None
        self._stroke_center_world = None
        self._preview_geometry = None
        self._dragging = False
        self._object_mode = context.mode == 'OBJECT'
        self._point_mode = context.mode == 'EDIT_POINTCLOUD'
        self._object = None if self._object_mode else context.active_object
        self._region = context.region
        self._region_data = context.region_data
        if self._region is None or self._region.type != 'WINDOW' or self._region_data is None:
            self.report({'WARNING'}, "Sphere Select must be started in a 3D Viewport")
            return {'CANCELLED'}
        state = preview.state_for(context)
        preview.track_cursor(context, event, self)
        self._bm, self._bvh, self._kdtree = state._bm, state._bvh, state._kdtree
        self._face_triangles = getattr(state, '_face_triangles', {})
        self._matrix_world = state._matrix_world
        self._scene_index = getattr(state, '_scene_index', None)
        self._points = getattr(state, '_points', None)
        self._topology_signature = getattr(state, '_topology_signature', None)
        for name in ('_query_key', '_query_result', '_preview_cache_key', '_preview_cache_geometry'):
            setattr(self, name, getattr(state, name, None))
        count = state._count
        if count == 0:
            self.report({'WARNING'}, "No visible selectable geometry")
            return {'CANCELLED'}
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(self._draw_callback, (), 'WINDOW', 'POST_VIEW')
        preview.busy.add(context.area.as_pointer())
        context.window_manager.modal_handler_add(self)
        self._update_hit(context, event)
        # The activation click begins the first brush stroke, matching a native
        # paint-tool flow instead of requiring a disposable entry click.
        if event.value == 'PRESS' and not self.wait_for_input:
            self._begin_stroke(context, event)
            self._paint_segment(context)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        from ..tools import preview
        preview.track_cursor(context, event, self)
        invalid = (context.mode != 'OBJECT') if self._object_mode else (
            context.active_object != self._object or self._object is None or self._object.mode != 'EDIT')
        if invalid or not self._topology_is_current():
            self.report({'INFO'}, "Sphere Select stopped because the edited geometry changed")
            return self._finish(context, cancelled=True)
        if self.continuous:
            if not self._dragging:
                hover_operation = 'SUB' if getattr(event, 'shift', False) else 'ADD'
                if self.operation != hover_operation:
                    self.operation = hover_operation
                    context.area.tag_redraw()
            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.value == 'PRESS':
                settings = self._settings(context)
                factor = WHEEL_RADIUS_FACTOR if event.type == 'WHEELUPMOUSE' else 1.0 / WHEEL_RADIUS_FACTOR
                settings.radius = max(0.000001, settings.radius * factor)
                self._preview_cache_key = None
                if self._center_world is not None:
                    self._preview_geometry = self._preview_geometry_for_mode(self._center_world, settings.radius)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            # Discard only the uncommitted stroke; prior released strokes remain.
            self._dragging = False
            return self._finish(context, cancelled=not self.continuous)
        if self.wait_for_input and not self._dragging and event.type in ({'LEFTMOUSE', 'MIDDLEMOUSE'} if self.continuous else {'LEFTMOUSE'}) and event.value == 'PRESS':
            self._release_key = event.type
            if self.continuous:
                self.operation = 'SUB' if (getattr(event, 'shift', False) or event.type == 'MIDDLEMOUSE') else 'ADD'
            self._update_hit(context, event)
            self._begin_stroke(context, event)
            self._paint_segment(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if self._dragging and event.type == self._release_key and event.value == 'RELEASE':
            if self.continuous:
                self._commit_stroke(context)
                self.operation = 'SUB' if getattr(event, 'shift', False) else 'ADD'
                self._stroke_center_world = None
                self._preview_cache_key = None
                if self._center_world is not None:
                    self._preview_geometry = self._preview_geometry_for_mode(self._center_world, self._settings(context).radius)
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            return self._finish(context)
        if event.type == 'MOUSEMOVE':
            hit = self._update_hit(context, event)
            if self._dragging and hit:
                self._paint_segment(context)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        # A temporary Select-menu session is a real modal selection tool, like
        # Blender's Circle Select: while it is waiting for strokes it owns input
        # so arbitrary viewport/editor shortcuts cannot run underneath it. The
        # toolbar's one-stroke invocation remains permissive after its stroke.
        return {'RUNNING_MODAL'} if self.continuous else {'PASS_THROUGH'}
