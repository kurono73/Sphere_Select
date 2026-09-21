"""Lazy per-object broad/narrow phase. Never combine scene meshes into a BMesh."""
import numpy as np
from math import sin, cos, pi
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def signature(context):
    return tuple((o.as_pointer(), tuple(v for row in o.matrix_world for v in row))
                 for o in context.visible_objects if not o.hide_select)


def aabb_distance(center, lo, hi):
    return sum(max(lo[i]-center[i], 0, center[i]-hi[i])**2 for i in range(3))


def ray_box(origin, direction, lo, hi, limit=float('inf')):
    near, far = 0.0, limit
    for axis in range(3):
        if abs(direction[axis]) < 1e-12:
            if origin[axis] < lo[axis] or origin[axis] > hi[axis]:
                return False
        else:
            a = (lo[axis]-origin[axis])/direction[axis]
            b = (hi[axis]-origin[axis])/direction[axis]
            near, far = max(near, min(a,b)), min(far, max(a,b))
            if near > far:
                return False
    return True


def display_proxy(obj, evaluated):
    """World-space display geometry for objects that have no renderable mesh."""
    positions, edges = [(0,0,0)], []
    def segment(a,b):
        n = len(positions)
        positions.extend((a,b))
        edges.append((n,n+1))
    def circle(radius, axes=(0,1)):
        n = len(positions)
        for i in range(48):
            p = [0,0,0]
            p[axes[0]],p[axes[1]] = radius*cos(2*pi*i/48),radius*sin(2*pi*i/48)
            positions.append(p)
            edges.append((n+i,n+(i+1)%48))
    if obj.type == 'CAMERA':
        corners = obj.data.view_frame()
        scale = obj.data.display_size / max(abs(v.z) for v in corners)
        corners = [v*scale for v in corners]
        for i in range(4):
            segment((0,0,0),corners[i])
            segment(corners[i],corners[(i+1)%4])
    elif obj.type == 'ARMATURE':
        for bone in obj.data.bones:
            if not bone.hide:
                segment(bone.head_local,bone.tail_local)
    elif obj.type == 'EMPTY':
        size = obj.empty_display_size
        kind = obj.empty_display_type
        if kind in {'CIRCLE','SPHERE'}:
            circle(size)
            if kind == 'SPHERE':
                circle(size,(0,2)); circle(size,(1,2))
        elif kind == 'CUBE':
            corners = [Vector((x,y,z))*size for x in (-1,1) for y in (-1,1) for z in (-1,1)]
            for i in range(8):
                for bit in (1,2,4):
                    if i < i^bit:
                        segment(corners[i],corners[i^bit])
        elif kind == 'IMAGE':
            corners=[(-size/2,-size/2,0),(size/2,-size/2,0),(size/2,size/2,0),(-size/2,size/2,0)]
            for i in range(4):
                segment(corners[i],corners[(i+1)%4])
        else:
            for axis in range(3):
                a,b=[0,0,0],[0,0,0]
                a[axis],b[axis] = -size,size
                segment(a,b)
    elif obj.type == 'LIGHT':
        data = obj.data
        size = getattr(data,'size',getattr(data,'shadow_soft_size',0.1))
        circle(max(size*0.5,0.01))
    else:
        corners = [Vector(c) for c in evaluated.bound_box]
        if len(set(tuple(c) for c in corners)) > 1:
            for i in range(8):
                for j in range(i+1,8):
                    if sum(abs(corners[i][k]-corners[j][k]) > 1e-8 for k in range(3)) == 1:
                        segment(corners[i],corners[j])
    return np.array([obj.matrix_world @ Vector(p) for p in positions]), np.array(edges,dtype=np.int32).reshape(-1,2)


class Entry:
    def __init__(self, obj, context, depsgraph=None):
        self.obj = obj
        self.evaluated = obj.evaluated_get(depsgraph if depsgraph is not None else context.evaluated_depsgraph_get())
        self.matrix = obj.matrix_world.copy()
        self.origin = self.matrix.translation.copy()
        corners = [self.matrix @ Vector(c) for c in self.evaluated.bound_box]
        if all(tuple(c) == (-1.0,-1.0,-1.0) for c in self.evaluated.bound_box):
            corners = [self.origin]
        self.lo = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
        self.hi = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
        self.geometric = obj.type in {'MESH','CURVE','SURFACE','FONT','META','CURVES','POINTCLOUD'}
        self.loaded = False
        self.bvh = None
        self.positions = None
        self.edges = None
        self.surface = None
        self.points = None
        if not self.geometric:
            self.positions,self.edges = display_proxy(obj,self.evaluated)
            self.lo = Vector(self.positions.min(axis=0))
            self.hi = Vector(self.positions.max(axis=0))

    def load(self):
        if self.loaded:
            return
        self.loaded = True
        if self.obj.type == 'POINTCLOUD':
            from .point_cloud import PointIndex
            self.points = PointIndex(self.evaluated.data, self.matrix)
            return
        if not self.geometric:
            return
        try:
            mesh = self.evaluated.to_mesh()
        except (RuntimeError, TypeError):
            mesh = None
        if mesh is None:
            self.geometric = False
            self.positions,self.edges = display_proxy(self.obj,self.evaluated)
            return
        try:
            coords = np.empty((len(mesh.vertices),3), dtype=np.float32)
            mesh.vertices.foreach_get('co', coords.ravel())
            matrix = np.array(self.matrix)
            coords = coords @ matrix[:3,:3].T + matrix[:3,3]
            mesh.calc_loop_triangles()
            triangles = np.empty((len(mesh.loop_triangles),3),dtype=np.int32)
            mesh.loop_triangles.foreach_get('vertices', triangles.ravel())
            self.positions = coords
            if len(triangles):
                self.bvh = BVHTree.FromPolygons(coords, triangles, all_triangles=True)
            # Only loose edges need a separate intersection test.
            counts = np.zeros(len(mesh.edges), dtype=np.int32)
            loop_edges = np.empty(len(mesh.loops),dtype=np.int32)
            mesh.loops.foreach_get('edge_index',loop_edges)
            if len(loop_edges):
                counts[loop_edges] = 1
            edges = np.empty((len(mesh.edges),2),dtype=np.int32)
            mesh.edges.foreach_get('vertices',edges.ravel())
            self.edges = edges[counts == 0]
            # Dense hover shading is visual only; use an origin marker for huge objects.
            if len(triangles) <= 150000:
                self.surface = {'positions': coords, 'triangles': triangles, 'batch': None}
        finally:
            self.evaluated.to_mesh_clear()

    def intersects(self, center, radius):
        if aabb_distance(center,self.lo,self.hi) > radius*radius:
            return False
        self.load()
        if self.points is not None:
            return bool(len(self.points.query(center, radius, include_radius=True)))
        if self.bvh is not None:
            hit = self.bvh.find_nearest(center, radius)
            if hit[0] is not None:
                return True
        if self.positions is not None and len(self.positions):
            c = np.asarray(center)
            if self.edges is not None and len(self.edges):
                a, b = self.positions[self.edges[:,0]], self.positions[self.edges[:,1]]
                ab = b-a
                t = np.clip(np.einsum('ij,ij->i',c-a,ab)/np.maximum(np.einsum('ij,ij->i',ab,ab),1e-30),0,1)
                delta = a+t[:,None]*ab-c
                if np.any(np.einsum('ij,ij->i',delta,delta) <= radius*radius):
                    return True
            delta = self.positions-c
            return bool(np.any(np.einsum('ij,ij->i',delta,delta) <= radius*radius))
        return (self.origin-center).length <= radius

    def ray_proxy(self, origin, direction, tolerance):
        if self.positions is None:
            return None
        c,d = np.asarray(origin),np.asarray(direction)
        candidates = self.positions
        if self.edges is not None and len(self.edges):
            a,b = self.positions[self.edges[:,0]],self.positions[self.edges[:,1]]
            ab = b-a
            w = c-a
            parallel = ab @ d
            denom = np.einsum('ij,ij->i',ab,ab)-parallel**2
            u = np.clip((np.einsum('ij,ij->i',ab,w)-parallel*(w@d))/np.maximum(denom,1e-20),0,1)
            candidates = np.concatenate((candidates,a+u[:,None]*ab))
        offset = candidates-c
        t = offset @ d
        delta = offset-t[:,None]*d
        valid = (t >= 0) & (np.einsum('ij,ij->i',delta,delta) <= tolerance*tolerance)
        if not valid.any():
            return None
        i = np.flatnonzero(valid)[np.argmin(t[valid])]
        return Vector(candidates[i]), -direction, 0, float(t[i])


class SceneIndex:
    def __init__(self, context):
        # Fetch/evaluate once for the scene, not once for every visible object.
        depsgraph = context.evaluated_depsgraph_get()
        self.entries = [Entry(obj,context,depsgraph) for obj in context.visible_objects if not obj.hide_select]
        self._candidate_key = None
        self._candidate_result = []

    def candidates(self, center, radius):
        key = (tuple(center), radius)
        if self._candidate_key != key:
            self._candidate_result = [entry for entry in self.entries if entry.intersects(center,radius)]
            self._candidate_key = key
        return self._candidate_result

    def preview(self, center, radius):
        entries = self.candidates(center,radius)
        return {'objects':[e.surface for e in entries if e.surface is not None],
                'markers':[e.origin for e in entries if e.surface is None]}

    def raycast(self, origin, direction, radius):
        best = None
        distance = float('inf')
        for entry in self.entries:
            # A small picking proxy makes non-surface object origins reachable.
            tolerance = max(radius*0.08, 0.01)
            padding = Vector((tolerance,)*3)
            if not ray_box(origin,direction,entry.lo-padding,entry.hi+padding,distance):
                continue
            entry.load()
            if entry.bvh:
                hit = entry.bvh.ray_cast(origin,direction,distance)
            elif entry.points is not None:
                hit = entry.points.raycast(origin,direction)
            else:
                hit = entry.ray_proxy(origin,direction,tolerance)
                if hit is None:
                    t = (entry.origin-origin).dot(direction)
                    hit = (entry.origin, -direction, 0, t) if t >= 0 and (origin+t*direction-entry.origin).length <= tolerance else (None,None,None,None)
            if hit is None:
                continue
            if hit[0] is not None and hit[3] < distance:
                best, distance = hit, hit[3]
        return best
