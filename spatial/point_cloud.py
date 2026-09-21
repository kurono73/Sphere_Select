"""Point Cloud Edit Mode selection and spatial search (Blender 5.x).

Uses Blender's bundled NumPy; no external package installation.
"""
import numpy as np
from mathutils import Vector


class PointIndex:
    def __init__(self, data, matrix):
        self.data = data
        self.count = len(data.points)
        self.positions = np.empty((self.count,3),dtype=np.float32)
        data.points.foreach_get('co',self.positions.ravel())
        transform = np.array(matrix)
        self.positions = self.positions @ transform[:3,:3].T + transform[:3,3]
        self.radii = np.empty(self.count,dtype=np.float32)
        data.points.foreach_get('radius',self.radii)
        self.radii = np.maximum(self.radii * max(abs(v) for v in matrix.to_scale()), 0.001)
        hidden = data.attributes.get('.hide')
        self.hidden = np.zeros(self.count,dtype=np.bool_)
        if hidden:
            hidden.data.foreach_get('value',self.hidden)
        self.root = self._build(np.flatnonzero(~self.hidden))

    def _build(self, ids):
        if not len(ids):
            return None
        points = self.positions[ids]
        lo, hi = points.min(axis=0), points.max(axis=0)
        max_radius = float(self.radii[ids].max())
        if len(ids) <= 512:
            return lo, hi, max_radius, ids, None, None
        axis = int(np.argmax(hi-lo))
        split = (lo[axis]+hi[axis])*0.5
        mask = points[:,axis] < split
        if not mask.any() or mask.all():
            return lo, hi, max_radius, ids, None, None
        return lo, hi, max_radius, None, self._build(ids[mask]), self._build(ids[~mask])

    def query(self, center, radius, include_radius=False):
        center = np.asarray(center)
        stack, found = [self.root], []
        while stack:
            node = stack.pop()
            if node is None:
                continue
            lo,hi,pad,ids,left,right = node
            broad = radius+(pad if include_radius else 0)
            delta = np.maximum(np.maximum(lo-center,center-hi),0)
            if delta.dot(delta) > broad*broad:
                continue
            if ids is None:
                stack.extend((left,right))
            else:
                delta = self.positions[ids]-center
                r = radius+self.radii[ids] if include_radius else radius
                found.append(ids[np.einsum('ij,ij->i',delta,delta) <= r*r])
        return np.concatenate(found) if found else np.empty(0,dtype=np.int64)

    def raycast(self, origin, direction):
        from .object_mesh import ray_box
        origin, direction = np.asarray(origin), np.asarray(direction)
        stack, best, distance = [self.root], None, float('inf')
        while stack:
            node = stack.pop()
            if node is None:
                continue
            lo,hi,pad,ids,left,right = node
            if not ray_box(origin,direction,lo-pad,hi+pad,distance):
                continue
            if ids is None:
                stack.extend((left,right))
                continue
            offsets = self.positions[ids]-origin
            along = offsets @ direction
            perpendicular = offsets-along[:,None]*direction
            d2 = np.einsum('ij,ij->i',perpendicular,perpendicular)
            valid = (along >= 0) & (d2 <= self.radii[ids]**2)
            if not valid.any():
                continue
            ts = along[valid]-np.sqrt(np.maximum(0,self.radii[ids[valid]]**2-d2[valid]))
            index = int(np.argmin(ts))
            t = max(0,float(ts[index]))
            if t < distance:
                point_id = ids[valid][index]
                distance = t
                best = (Vector(self.positions[point_id]), Vector(-direction), int(point_id), t)
        return best

    def apply(self, center, radius, select):
        return self.apply_samples([center],radius,select)

    def apply_samples(self, centers, radius, select):
        if not centers:
            return 0
        ids = np.unique(np.concatenate([self.query(center,radius) for center in centers]))
        return self.apply_ids(ids, select)

    def apply_ids(self, ids, select):
        ids = np.asarray(ids, dtype=np.int64)
        if len(ids) == 0:
            return 0
        attr = self.data.attributes.get('.selection')
        if attr is None:
            attr = self.data.attributes.new('.selection','BOOLEAN','POINT')
            values = np.ones(self.count,dtype=np.bool_)
            attr.data.foreach_set('value',values)
        values = np.empty(self.count,dtype=np.bool_ if attr.data_type == 'BOOLEAN' else np.float32)
        attr.data.foreach_get('value',values)
        values[ids] = select
        attr.data.foreach_set('value',values)
        return len(ids)

    def clear(self):
        attr = self.data.attributes.get('.selection')
        if attr is None:
            attr = self.data.attributes.new('.selection','BOOLEAN','POINT')
            values = np.ones(self.count,dtype=np.bool_)
        else:
            values = np.empty(self.count,dtype=np.bool_ if attr.data_type == 'BOOLEAN' else np.float32)
            attr.data.foreach_get('value',values)
        values[~self.hidden] = False
        attr.data.foreach_set('value',values)
