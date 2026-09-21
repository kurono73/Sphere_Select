# Sphere_Select

# Overview

Sphere Select adds a **surface-snapped 3D selection brush** to the Blender viewport.

Unlike Circle Select, which selects using a screen-space region, Sphere Select uses a finite sphere positioned directly on the visible surface. This makes the affected area easy to understand in 3D and prevents unintended selection of geometry outside the brush volume.

Sphere Select is designed for fast interaction with **dense geometry**. On high-poly meshes, it can provide lighter and more responsive selection than Circle Select, making it particularly useful for cleaning up **3D scans, photogrammetry meshes, and other high-density geometry**.

It integrates alongside Blender's standard selection tools and supports **Object Mode, Mesh Edit Mode, and Point Cloud Edit Mode**.

## Features

- True **3D spherical volume selection**
- Surface-snapped brush for intuitive positioning
- **Surface Lock** to reduce unexpected jumps across gaps and disconnected surfaces
- Optimized selection for dense and high-poly geometry
- Object selection based on intersection with the 3D brush volume
- Vertex, Edge, and Face selection in Mesh Edit Mode
- Native point selection in Point Cloud Edit Mode
- Visual preview of the 3D selection volume and affected geometry
- Set, Extend, and Subtract selection modes
- No external dependencies

---

## Where to Find It

- **3D Viewport Toolbar:** `Select Sphere`
- **Select Menu:** `Header > Select > Sphere Select`

## Controls

Sphere Select follows the **default Circle Select keymap and interaction behavior** in both the Toolbar and Select menu, providing familiar controls for a true 3D selection brush.

Keyboard and mouse shortcuts can be customized through Blender's **Keymap preferences**.

### Toolbar

Like `Select Circle`, the toolbar tool remains active for repeated selection strokes.

Choose **Set**, **Extend**, or **Subtract** as the selection mode.

- **Shift** — Temporarily Extend
- **Ctrl** — Temporarily Subtract

Drag across the surface to paint a 3D selection. The selection is committed when the mouse button is released.

### Select Menu / Shortcut

Like `Circle Select` launched from the Select menu, Sphere Select starts a continuous selection session.

- **Drag** — Extend selection
- **Shift + Drag** — Subtract selection
- **Middle Mouse + Drag** — Subtract selection
- **Mouse Wheel** — Adjust brush radius
- **Right Mouse / Esc** — Finish

Each mouse release commits one selection stroke.

---
## Supported Modes

### Object Mode

Objects are selected when their geometry intersects the sphere, allowing the brush to select objects by spatial contact rather than only by their origin.

### Mesh Edit Mode

Works with Blender's Vertex, Edge, and Face selection modes.

### Point Cloud Edit Mode

Directly selects native Point Cloud points without converting the data to a mesh.
