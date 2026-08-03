"""Run inside Blender: independently validate every required deliverable."""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import bpy


def args() -> tuple[Path, dict, Path]:
    values = sys.argv[sys.argv.index("--") + 1 :]
    root, config, report = Path(values[0]), Path(values[1]), Path(values[2])
    return root.resolve(), json.loads(config.read_text(encoding="utf-8")), report.resolve()


def clear() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for block in list(collection):
            if block.users == 0:
                collection.remove(block)


def import_asset(path: Path) -> None:
    if path.suffix == ".glb":
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=str(path))
    else:
        bpy.ops.import_scene.obj(filepath=str(path))


def inspect(path: Path, profile: dict) -> dict:
    clear()
    errors = []
    try:
        import_asset(path)
    except Exception as error:
        return {"path": str(path), "valid": False, "errors": [f"import failed: {error}"]}
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    materials = {slot.material for obj in meshes for slot in obj.material_slots if slot.material}
    triangles = 0
    finite = True
    bounds_min = [math.inf, math.inf, math.inf]
    bounds_max = [-math.inf, -math.inf, -math.inf]
    for obj in meshes:
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
        for vertex in obj.data.vertices:
            finite = finite and all(math.isfinite(value) for value in vertex.co)
        finite = finite and all(math.isfinite(value) for row in obj.matrix_world for value in row)
        for vertex in obj.data.vertices:
            point = obj.matrix_world @ vertex.co
            for axis in range(3):
                bounds_min[axis] = min(bounds_min[axis], point[axis])
                bounds_max[axis] = max(bounds_max[axis], point[axis])
    textures = [image for image in bpy.data.images if image.type == "IMAGE" and image.size[0] > 0]
    dimensions = [[int(image.size[0]), int(image.size[1])] for image in textures]
    if not meshes:
        errors.append("no mesh")
    if triangles <= 0:
        errors.append("no triangles")
    if not materials:
        errors.append("no material")
    if triangles > int(profile["max_triangles"]):
        errors.append(f"triangle limit exceeded: {triangles}")
    if len(textures) > int(profile["max_texture_atlases"]):
        errors.append(f"texture atlas limit exceeded: {len(textures)}")
    if any(max(size) > int(profile["max_texture_dimension"]) for size in dimensions):
        errors.append("texture dimension limit exceeded")
    if not finite:
        errors.append("non-finite transform or geometry")
    bounds = None
    if all(math.isfinite(value) for value in (*bounds_min, *bounds_max)):
        bounds = {
            "min": bounds_min,
            "max": bounds_max,
            "size": [bounds_max[axis] - bounds_min[axis] for axis in range(3)],
        }
    return {
        "path": str(path), "valid": not errors, "errors": errors, "meshes": len(meshes),
        "materials": len(materials), "triangles": triangles, "textures": len(textures), "texture_dimensions": dimensions,
        "bounds": bounds,
    }


def validate_obj_paths(obj: Path) -> list[str]:
    errors = []
    mtllibs = []
    for line in obj.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith("mtllib "):
            mtllibs.append(line.split(maxsplit=1)[1].strip())
    if not mtllibs:
        return ["OBJ has no relative MTL reference"]
    for value in mtllibs:
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            errors.append(f"non-portable MTL path: {value}")
            continue
        mtl = obj.parent / candidate
        if not mtl.exists():
            errors.append(f"missing MTL: {value}")
            continue
        for line in mtl.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^(map_|bump|disp|decal)", line, re.IGNORECASE):
                value = line.split()[-1]
                texture = Path(value)
                if texture.is_absolute() or ".." in texture.parts or not (mtl.parent / texture).exists():
                    errors.append(f"missing or non-portable texture path: {value}")
    return errors


def main() -> None:
    root, config, report_path = args()
    result = {"valid": True, "profiles": {}}
    for name in ("high", "medium", "low"):
        profile_result = {"assets": []}
        for suffix in ("glb", "obj"):
            path = root / name / f"{name}.{suffix}"
            checked = inspect(path, config["profiles"][name]) if path.exists() else {"path": str(path), "valid": False, "errors": ["missing"]}
            if suffix == "obj" and path.exists():
                checked["errors"].extend(validate_obj_paths(path))
                checked["valid"] = not checked["errors"]
            profile_result["assets"].append(checked)
        profile_result["valid"] = all(item["valid"] for item in profile_result["assets"])
        result["profiles"][name] = profile_result
        result["valid"] = result["valid"] and profile_result["valid"]
    high_size = result["profiles"]["high"]["assets"][0].get("bounds", {}).get("size") if result["profiles"]["high"]["assets"][0].get("bounds") else None
    if high_size:
        for name in ("medium", "low"):
            asset = result["profiles"][name]["assets"][0]
            size = asset.get("bounds", {}).get("size") if asset.get("bounds") else None
            if not size or any(reference > 0 and abs(value / reference - 1.0) > 0.05 for value, reference in zip(size, high_size)):
                asset["errors"].append("orientation/relative-scale bounds differ from high profile by more than 5%")
                asset["valid"] = False
                result["profiles"][name]["valid"] = False
                result["valid"] = False
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


main()
