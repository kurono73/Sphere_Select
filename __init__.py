"""Surface-snapped 3D sphere selection for Blender viewports."""

bl_info = {
    "name": "Sphere Select",
    "author": "CGS Lab",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Toolbar > Select Sphere / Select > Sphere Select",
    "description": "Surface-snapped finite 3D sphere selection",
    "category": "3D View",
}

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty
from bpy.types import PropertyGroup

from .operators.volume_select_brush import VIEW3D_OT_sphere_select
from .tools.toolbar import VIEW3D_TT_sphere_select, VIEW3D_TT_sphere_select_objects, VIEW3D_TT_sphere_select_points
from .tools import preview
from .tools.menu import VIEW3D_OT_sphere_select_activate, draw_menu


class SphereSelectSettings(PropertyGroup):
    radius: FloatProperty(
        name="Radius", description="World-space sphere radius", default=0.1,
        min=0.000001, soft_max=1000.0, subtype='DISTANCE', unit='LENGTH')
    surface_lock: BoolProperty(
        name="Surface Lock", description="Keep the brush from jumping across gaps",
        default=True)
    selection_operation: EnumProperty(
        name="Mode", description="How each brush stroke changes the current selection",
        items=(
            ('SET', "Set", "Set a new selection", 'SELECT_SET', 0),
            ('ADD', "Extend", "Extend the existing selection", 'SELECT_EXTEND', 1),
            ('SUB', "Subtract", "Subtract from the existing selection", 'SELECT_SUBTRACT', 2),
        ),
        default='SET')


CLASSES = (SphereSelectSettings, VIEW3D_OT_sphere_select, VIEW3D_OT_sphere_select_activate)
_registered = False


def register():
    global _registered
    if _registered:
        return
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.sphere_select_settings = PointerProperty(type=SphereSelectSettings)
    bpy.utils.register_tool(VIEW3D_TT_sphere_select, after={"builtin.select_box"}, separator=True)
    bpy.utils.register_tool(VIEW3D_TT_sphere_select_objects, after={"builtin.select_box"}, separator=True)
    if hasattr(bpy.types, 'VIEW3D_MT_select_edit_pointcloud'):
        bpy.utils.register_tool(VIEW3D_TT_sphere_select_points, after={"builtin.select_box"}, separator=True)
        bpy.types.VIEW3D_MT_select_edit_pointcloud.append(draw_menu)
    preview.register()
    bpy.types.VIEW3D_MT_select_edit_mesh.append(draw_menu)
    bpy.types.VIEW3D_MT_select_object.append(draw_menu)
    _registered = True


def unregister():
    global _registered
    if not _registered:
        return
    if hasattr(bpy.types, 'VIEW3D_MT_select_edit_pointcloud'):
        bpy.types.VIEW3D_MT_select_edit_pointcloud.remove(draw_menu)
        bpy.utils.unregister_tool(VIEW3D_TT_sphere_select_points)
    bpy.types.VIEW3D_MT_select_object.remove(draw_menu)
    bpy.types.VIEW3D_MT_select_edit_mesh.remove(draw_menu)
    preview.unregister()
    bpy.utils.unregister_tool(VIEW3D_TT_sphere_select)
    bpy.utils.unregister_tool(VIEW3D_TT_sphere_select_objects)
    del bpy.types.WindowManager.sphere_select_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _registered = False
