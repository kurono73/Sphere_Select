"""Edit Mode mesh BVH construction and view-ray queries."""

from mathutils import Vector
from mathutils.bvhtree import BVHTree


def build_edit_mesh_bvh(bm):
    """Build a BMesh BVH in object-local coordinates; callers skip hidden hits."""
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    if not bm.faces:
        return None
    # Native construction avoids a second Python copy of every vertex/polygon.
    return BVHTree.FromBMesh(bm)


def raycast_local(bvh, origin, direction):
    if bvh is None:
        return None
    location, normal, face_index, distance = bvh.ray_cast(Vector(origin), Vector(direction))
    if location is None:
        return None
    return location, normal, face_index, distance
