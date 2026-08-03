"""Run inside Blender: create bounded textured GLB and OBJ profile variants."""
from __future__ import annotations

import json
import math
import shutil
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


def arguments() -> tuple[Path, Path, Path]:
    argv = sys.argv[sys.argv.index("--") + 1 :]
    if len(argv) != 3:
        raise SystemExit("expected: source.obj output_directory profiles.json")
    return Path(argv[0]).resolve(), Path(argv[1]).resolve(), Path(argv[2]).resolve()


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def import_obj(path: Path) -> None:
    if bpy.app.version >= (3, 3, 0):
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        bpy.ops.import_scene.obj(filepath=str(path))


def mesh_objects() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def triangle_count() -> int:
    total = 0
    for obj in mesh_objects():
        dependency = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(dependency)
        mesh = evaluated.to_mesh()
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
        evaluated.to_mesh_clear()
    return total


def join_meshes() -> bpy.types.Object:
    meshes = mesh_objects()
    if not meshes:
        raise RuntimeError("source OBJ contains no mesh")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def center_geometry(obj: bpy.types.Object) -> list[float]:
    corners = [obj.matrix_world @ Vector(obj.bound_box[index]) for index in range(8)]
    center = sum(corners, corners[0].copy() * 0) / 8
    obj.data.transform(obj.matrix_world)
    obj.matrix_world.identity()
    obj.data.transform(
        Matrix((
            (1, 0, 0, -center.x),
            (0, 1, 0, -center.y),
            (0, 0, 1, -center.z),
            (0, 0, 0, 1),
        ))
    )
    return [float(center.x), float(center.y), float(center.z)]


def decimate(obj: bpy.types.Object, target: int) -> None:
    current = triangle_count()
    if current <= target:
        return
    modifier = obj.modifiers.new(name="Profile triangle limit", type="DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = max(0.0001, min(1.0, target / current))
    modifier.use_collapse_triangulate = True
    modifier.use_symmetry = False
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.modifier_apply(modifier=modifier.name)


def resize_textures(profile_dir: Path, maximum: int, texture_format: str, quality: int) -> None:
    for image in list(bpy.data.images):
        if image.type != "IMAGE" or image.size[0] == 0 or image.size[1] == 0:
            continue
        if max(image.size) > maximum:
            ratio = maximum / max(image.size)
            image.scale(max(1, int(image.size[0] * ratio)), max(1, int(image.size[1] * ratio)))
        suffix = ".jpg" if texture_format.lower() in {"jpg", "jpeg"} else ".png"
        target = profile_dir / "textures" / f"{image.name_full.replace('/', '_')}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        image.filepath_raw = str(target)
        image.file_format = "JPEG" if suffix == ".jpg" else "PNG"
        if suffix == ".jpg":
            image.save_render(str(target), quality=quality)
        else:
            image.save()


def export_obj(path: Path) -> None:
    if bpy.app.version >= (3, 3, 0):
        bpy.ops.wm.obj_export(filepath=str(path), export_materials=True, path_mode="RELATIVE")
    else:
        bpy.ops.export_scene.obj(filepath=str(path), use_materials=True, path_mode="RELATIVE")


def export_glb(path: Path) -> None:
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        export_apply=True,
        export_materials="EXPORT",
        export_texcoords=True,
        export_normals=True,
    )


def main() -> None:
    source, output, config_path = arguments()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    metrics = {}
    for name in ("high", "medium", "low"):
        profile = config["profiles"][name]
        profile_dir = output / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        clear_scene()
        import_obj(source)
        obj = join_meshes()
        center = center_geometry(obj) if config.get("center_at_origin", True) else [0.0, 0.0, 0.0]
        decimate(obj, int(profile["target_triangles"]))
        resize_textures(
            profile_dir,
            int(profile["max_texture_dimension"]),
            str(profile["texture_format"]),
            int(profile.get("jpeg_quality", 90)),
        )
        bpy.ops.object.select_all(action="SELECT")
        export_glb(profile_dir / f"{name}.glb")
        export_obj(profile_dir / f"{name}.obj")
        metrics[name] = {
            "triangles": triangle_count(),
            "center_translation_enu_or_arbitrary": [-value for value in center],
            "coordinate_conversion": "COLMAP/OpenGL world exported as Blender Z-up through Blender import/export",
            "units": "meters when GPS alignment succeeded; otherwise arbitrary",
        }
    (output / "profile-metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


main()
