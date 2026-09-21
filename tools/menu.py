"""Standard searchable menu operator; supports Assign Shortcut from the menu."""
import bpy


class VIEW3D_OT_sphere_select_activate(bpy.types.Operator):
    bl_idname = 'view3d.sphere_select_activate'
    bl_label = 'Sphere Select'
    bl_description = 'Select with a temporary sphere brush, keeping the current toolbar tool'

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D' and context.mode in {'EDIT_MESH', 'OBJECT', 'EDIT_POINTCLOUD'}

    def execute(self, context):
        region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        if region is None:
            return {'CANCELLED'}
        with context.temp_override(region=region):
            result = bpy.ops.view3d.sphere_select('INVOKE_DEFAULT', wait_for_input=True,
                                                     continuous=True, operation='ADD')
        return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}


def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator('view3d.sphere_select_activate')
