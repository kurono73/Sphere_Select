"""Mesh Edit Mode workspace tool registration."""

import bpy
from .preview import draw_cursor


class VIEW3D_TT_sphere_select(bpy.types.WorkSpaceTool):
    bl_idname = "sphere_select.tool"
    bl_label = "Select Sphere"
    bl_description = "Select mesh elements with a surface-snapped 3D sphere"
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_MESH'
    # Blender's generic selection-paint glyph keeps the dashed selection cue
    # while communicating that the sphere is painted across the surface.
    bl_icon = "ops.generic.select_paint"
    bl_widget = None
    draw_cursor = staticmethod(draw_cursor)
    bl_keymap = (
        ("view3d.sphere_select", {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        ("view3d.sphere_select", {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True}, {"properties": [("operation", 'ADD')]}),
        ("view3d.sphere_select", {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True}, {"properties": [("operation", 'SUB')]}),
    )

    @staticmethod
    def draw_settings(context, layout, tool):
        settings = context.window_manager.sphere_select_settings
        layout.prop(settings, "selection_operation", text="", expand=True, icon_only=True)
        layout.prop(settings, "radius")
        layout.prop(settings, "surface_lock")


class VIEW3D_TT_sphere_select_objects(bpy.types.WorkSpaceTool):
    bl_idname = 'sphere_select.object_tool'
    bl_label = 'Select Sphere'
    bl_description = 'Select objects intersecting a 3D sphere'
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_icon = 'ops.generic.select_paint'
    bl_widget = None
    draw_cursor = staticmethod(draw_cursor)
    bl_keymap = VIEW3D_TT_sphere_select.bl_keymap
    draw_settings = staticmethod(VIEW3D_TT_sphere_select.draw_settings)


class VIEW3D_TT_sphere_select_points(bpy.types.WorkSpaceTool):
    bl_idname = 'sphere_select.point_tool'
    bl_label = 'Select Sphere'
    bl_description = 'Select point cloud points inside a 3D sphere'
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'EDIT_POINTCLOUD'
    bl_icon = 'ops.generic.select_paint'
    bl_widget = None
    draw_cursor = staticmethod(draw_cursor)
    bl_keymap = VIEW3D_TT_sphere_select.bl_keymap
    draw_settings = staticmethod(VIEW3D_TT_sphere_select.draw_settings)
