"""KDTree for visible Edit Mode vertices."""

from mathutils.kdtree import KDTree


def build_visible_vertex_tree(bm):
    bm.verts.ensure_lookup_table()
    visible = [vert for vert in bm.verts if not vert.hide]
    if not visible:
        return None, 0
    tree = KDTree(len(visible))
    for vert in visible:
        tree.insert(vert.co, vert.index)
    tree.balance()
    return tree, len(visible)

