"""Workspace cursor preview: no modal operator or input interception while idle."""
from types import SimpleNamespace, MethodType
import bpy
import gpu
from bpy.app.handlers import persistent

_states = {}
busy = set()
_mouse = {}
_draw_handle = None
_pixel_handle = None
_cache_watch_active = False
active = {}
_selection_updates = set()


def selection_update(obj):
    _selection_updates.add(obj.as_pointer())
    _selection_updates.add(obj.data.as_pointer())


@persistent
def clear_cache(*_args):
    _states.clear()


def _start_cache_watch():
    """Watch only while a sphere-selection spatial cache exists."""
    global _cache_watch_active
    if _cache_watch_active:
        return
    if geometry_updated not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(geometry_updated)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post,
                     bpy.app.handlers.load_pre):
        if clear_cache not in handlers:
            handlers.append(clear_cache)
    _cache_watch_active = True


def _stop_cache_watch():
    global _cache_watch_active
    if not _cache_watch_active:
        return
    if geometry_updated in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(geometry_updated)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post,
                     bpy.app.handlers.load_pre):
        if clear_cache in handlers:
            handlers.remove(clear_cache)
    _cache_watch_active = False


def _ensure_draw_handlers():
    global _draw_handle, _pixel_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_view, (), 'WINDOW', 'POST_VIEW')
    if _pixel_handle is None:
        _pixel_handle = bpy.types.SpaceView3D.draw_handler_add(draw_pixel, (), 'WINDOW', 'POST_PIXEL')


def drop_cache_if_inactive(context):
    """Release cache watchers after a temporary menu session ends."""
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    if tool and tool.idname in {'sphere_select.tool', 'sphere_select.object_tool', 'sphere_select.point_tool'}:
        return
    clear_cache()
    _stop_cache_watch()


@persistent
def geometry_updated(scene, depsgraph):
    if any((update.is_updated_geometry or update.is_updated_transform) and update.id.original.as_pointer() not in _selection_updates
           for update in depsgraph.updates):
        clear_cache()
    _selection_updates.clear()


def state_for(context):
    from ..operators.volume_select_brush import VIEW3D_OT_sphere_select as Op
    key = (context.window.as_pointer(), context.area.as_pointer(), context.region.as_pointer())
    obj = context.edit_object
    object_mode = context.mode == 'OBJECT'
    state = _states.get(key)
    from ..spatial.object_mesh import signature
    if (state is None or state._object_mode != object_mode or state._object != obj
            or (state._bm is not None and not state._bm.is_valid) or not state._topology_is_current()
            or (object_mode and state._scene_signature != signature(context))):
        state = SimpleNamespace(_object=obj, _center_world=None, _previous_raw_world=None,
                                _normal_world=None, _locked_target_world=None, _lock_frames=0,
                                _dragging=False, _preview_geometry=None, _object_mode=object_mode,
                                _point_mode=context.mode == 'EDIT_POINTCLOUD',
                                _bm=None, _bvh=None, _kdtree=None)
        for name in ('_settings', '_topology_is_current', '_build_acceleration', '_local_ray',
                     '_update_hit', '_vertices_in_sphere', '_preview_geometry_for_mode', '_build_preview_geometry',
                     '_triangles_for_faces'):
            setattr(state, name, MethodType(getattr(Op, name), state))
        state._count = state._build_acceleration(context)
        _states[key] = state
    _ensure_draw_handlers()
    _start_cache_watch()
    state._region = context.region
    state._region_data = context.region_data
    return state


def draw_cursor(_context, _tool, xy):
    """Cursor callbacks run in window space, not the View3D depth framebuffer."""
    context = bpy.context
    _ensure_draw_handlers()
    if context.window and context.area and context.area.type == 'VIEW_3D':
        key = (context.window.as_pointer(), context.area.as_pointer())
        xy = tuple(xy)
        if _mouse.get(key) != xy:
            _mouse[key] = xy
            context.area.tag_redraw()


def track_cursor(context, event, state):
    if context.window is None or context.area is None:
        return
    key = (context.window.as_pointer(), context.area.as_pointer())
    if hasattr(event, 'mouse_x') and hasattr(event, 'mouse_y'):
        _mouse[key] = (event.mouse_x, event.mouse_y)
    elif hasattr(event, 'mouse_region_x') and hasattr(event, 'mouse_region_y'):
        _mouse[key] = (event.mouse_region_x+context.region.x, event.mouse_region_y+context.region.y)
    active[context.area.as_pointer()] = state


def draw_pixel():
    context = bpy.context
    if not context.area or not context.region or context.mode not in {'OBJECT','EDIT_MESH','EDIT_POINTCLOUD'}:
        return
    area = context.area.as_pointer()
    state = active.get(area)
    if state is None:
        tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        if not tool or tool.idname not in {'sphere_select.tool','sphere_select.object_tool','sphere_select.point_tool'}:
            return
        state = _states.get((context.window.as_pointer(),area,context.region.as_pointer()))
    xy = _mouse.get((context.window.as_pointer(),area))
    if xy is None:
        return
    x,y = xy[0]-context.region.x,xy[1]-context.region.y
    if 0 <= x < context.region.width and 0 <= y < context.region.height:
        from ..drawing.brush_overlay import draw_menu_radius_status, draw_status_cursor
        radius = context.window_manager.sphere_select_settings.radius
        pixels = status_radius(state, radius)
        if pixels is not None:
            draw_status_cursor(x,y,radius,True,pixels)
        elif getattr(state, 'continuous', False):
            draw_menu_radius_status(radius)


def status_radius(state, radius):
    # The actual sphere is the only cursor on a surface. In empty space, show
    # an indicative cursor without pretending to have a hit depth.
    if state is not None and getattr(state, '_center_world', None) is not None:
        return None
    return 12.0


def draw_view():
    """Render against the actual viewport matrices and scene depth attachment."""
    context = bpy.context
    from ..drawing.brush_overlay import draw_brush
    if context.mode not in {'EDIT_MESH', 'OBJECT', 'EDIT_POINTCLOUD'} or not context.region_data:
        return
    tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
    if not tool or tool.idname not in {'sphere_select.tool', 'sphere_select.object_tool', 'sphere_select.point_tool'}:
        drop_cache_if_inactive(context)
        return
    if context.area.as_pointer() in busy:
        return
    xy = _mouse.get((context.window.as_pointer(), context.area.as_pointer()))
    if xy is None:
        return
    x, y = xy[0] - context.region.x, xy[1] - context.region.y
    if not (0 <= x < context.region.width and 0 <= y < context.region.height):
        return
    try:
        state = state_for(context)
    except (ReferenceError, RuntimeError):
        return
    # Hover should track freely; Surface Lock applies to actual strokes.
    hover_key = (x,y,context.window_manager.sphere_select_settings.radius,
                 tuple(context.tool_settings.mesh_select_mode),
                 tuple(v for row in context.region_data.perspective_matrix for v in row))
    if getattr(state, '_hover_key', None) != hover_key:
        state._previous_raw_world = None
        state._center_world = None
        try:
            state._update_hit(context, SimpleNamespace(mouse_region_x=x, mouse_region_y=y))
        except (ReferenceError, RuntimeError):
            clear_cache()
            return
        state._hover_key = hover_key
    with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
        gpu.matrix.load_matrix(context.region_data.view_matrix)
        gpu.matrix.load_projection_matrix(context.region_data.window_matrix)
        draw_brush(state._center_world, state._normal_world,
                   context.window_manager.sphere_select_settings.radius,
                   state._view_direction_world, state._preview_geometry,
                   (0.08,1.0,0.22) if context.window_manager.sphere_select_settings.selection_operation == 'SUB' else None)


def register():
    """Handlers are installed lazily when the tool or menu session needs them."""


def unregister():
    global _draw_handle, _pixel_handle
    if _pixel_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_pixel_handle, 'WINDOW')
        except (ReferenceError, ValueError):
            pass
        _pixel_handle = None
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except (ReferenceError, ValueError):
            pass
        _draw_handle = None
    _mouse.clear()
    _stop_cache_watch()
    clear_cache()
    busy.clear()
    active.clear()
