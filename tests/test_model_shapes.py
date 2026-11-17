"""A product model may carry other shapes (glTF morph targets the viewer's switch shows). The page is mapped on
the model's UVs, so a target must move only positions and normals, one delta per vertex: then TEXCOORD_0 is the
same in every shape and the print lands where it does on the model as modelled.

Checks every .glb in the local templates/ folder that has a target; skips when none has.
Run: python3.13 -m pytest tests/test_model_shapes.py -q
"""
import glob
import json
import os
import struct

import pytest

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


def gltf_of(path):
    """The JSON chunk of a .glb."""
    with open(path, "rb") as handle:
        data = handle.read(20)
        length = struct.unpack_from("<I", data, 12)[0]
        return json.loads(handle.read(length))


SHAPED = [path for path in sorted(glob.glob(os.path.join(TEMPLATES, "*.glb")))
          if any("targets" in primitive for mesh in gltf_of(path).get("meshes", []) for primitive in mesh["primitives"])]


@pytest.mark.skipif(not SHAPED, reason="no model with morph targets in templates/")
@pytest.mark.parametrize("path", SHAPED, ids=os.path.basename)
def test_every_shape_moves_the_same_vertices_and_leaves_their_uvs(path):
    gltf = gltf_of(path)
    count = lambda accessor: gltf["accessors"][accessor]["count"]
    for mesh in gltf["meshes"]:
        for primitive in mesh["primitives"]:
            vertices = count(primitive["attributes"]["POSITION"])
            for target in primitive.get("targets", []):
                assert set(target) <= {"POSITION", "NORMAL"}
                assert all(count(accessor) == vertices for accessor in target.values())
            if "TEXCOORD_0" in primitive["attributes"]:
                assert count(primitive["attributes"]["TEXCOORD_0"]) == vertices
