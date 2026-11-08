"""Rebuild a Godot floor from a Sprite Fusion project and its gameplay manifest.

    python build_floor.py SOURCE.json FLOOR_DIR [--preview floor.png]

The manifest in FLOOR_DIR stores unique object IDs and coordinates in the
exported map's grid. Sprite Fusion stores placements in source pixel space.
"""
import argparse
import base64
import io
import json
import re
import struct
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def read_godot_layers(directory):
    scene = (directory / "Map.tscn").read_text(encoding="utf-8")
    layers = {}
    for match in re.finditer(r'^\[node name="([^"]+)" type="TileMapLayer"[^\n]*\]\n([^\[]*)', scene, re.M):
        name, body = match.groups()
        raw = re.search(r'tile_map_data = PackedByteArray\("([^"]*)"\)', body)
        if raw:
            data = base64.b64decode(raw[1])
            layers[name] = {(x, y) for x, y, *_ in struct.iter_unpack("<6h", data[2:])}
    return layers


def write_navigation(target, manifest, layers):
    w, h = manifest["size"]
    room_grid = [[0] * w for _ in range(h)]
    blocked = set(map(tuple, manifest.get("extra_blocked", [])))
    solids = [n for n in layers if n == "wall" or n.startswith("obstacle")]
    for name in manifest.get("solid_layers", solids):
        blocked.update(layers.get(name, ()))
    doors = {tuple(c) for obj in manifest["objects"] if obj["type"] == "door"
             for c in obj.get("cells", [obj["cell"]])}
    blocked.difference_update(doors)
    blocked.difference_update(map(tuple, manifest.get("walkable_overrides", [])))
    for index, room in enumerate(manifest["rooms"], 1):
        all_cells = layers[room["layer"]]
        walk = all_cells - blocked
        if not walk:
            raise ValueError(f'Room {room["id"]} has no walkable cells')
        for x, y in walk:
            if room_grid[y][x]:
                raise ValueError(f'Overlapping room floors at {(x, y)}')
            room_grid[y][x] = index
    # A doorway may be drawn over an intentional gap between two floor layers.
    for obj in manifest["objects"]:
        if obj["type"] == "door":
            room_no = next(i for i,r in enumerate(manifest["rooms"],1) if r["id"] == obj["room"])
            for x,y in obj.get("cells", [obj["cell"]]):
                if not room_grid[y][x]:
                    room_grid[y][x] = room_no
    grid = dict(tile_size=manifest["tile_size"], origin=[0, 0], size=[w,h],
                room_grid=room_grid, door_cells=sorted(doors),
                obstacle=["".join("0" if cell else "1" for cell in row) for row in room_grid])
    target.mkdir(parents=True, exist_ok=True)
    save(target / "grid.json", grid)


def existing_atlas(directory, tile_size):
    """Keep the coordinates and resource settings referenced by the original split scenes."""
    from PIL import Image

    if not (directory / "ground_6.tscn").exists():
        return 32, [], ""
    resource = (directory / "tileset.tres").read_text(encoding="utf-8")
    with Image.open(directory / "tileset.png") as image:
        atlas = image.convert("RGBA")
    columns = atlas.width // tile_size
    coords = {(int(x), int(y)) for x, y in re.findall(r"^(\d+):(\d+)/0 = 0$", resource, re.M)}
    solid = {(int(x), int(y)) for x, y in re.findall(r"^(\d+):(\d+)/0/physics_layer_0/polygon_0/points", resource, re.M)}
    textures = []
    for i in range(len(coords)):
        x, y = i % columns, i // columns
        if (x, y) not in coords:
            raise ValueError("Original atlas has non-contiguous tile coordinates")
        left, top = x * tile_size, y * tile_size
        textures.append((atlas.crop((left, top, left + tile_size, top + tile_size)), (x, y) in solid))
    return columns, textures, resource


def build(source, directory, preview=None):
    from PIL import Image

    project = load(source)
    manifest = load(directory / "manifest.json")
    tile_size = project["tileSize"]
    sheets = {key: Image.open(io.BytesIO(base64.b64decode(image["base64"].split(",")[-1]))).convert("RGBA")
              for key, image in project["spriteSheets"].items()}
    tiles = [t for layer in project["layers"] for t in layer["tiles"]]
    x0, y0 = (min(t[key] for t in tiles) // tile_size for key in ("x", "y"))
    # Keep the declared canvas when artwork is removed from its empty margin.
    # The navigation grid and the preview use this same size.
    canvas_w, canvas_h = manifest.get("size", (0, 0))
    w = max(canvas_w, max(t["x"] for t in tiles) // tile_size - x0 + 1)
    h = max(canvas_h, max(t["y"] for t in tiles) // tile_size - y0 + 1)
    atlas_columns, textures, existing_resource = existing_atlas(directory, tile_size)
    existing_count = len(textures)
    by_pixels = {(im.tobytes(), solid): i for i, (im, solid) in enumerate(textures)}
    atlas_index = {}
    layers, encoded = {}, []
    picture = Image.new("RGBA", (w * tile_size, h * tile_size)) if preview else None
    for layer in reversed(project["layers"]):
        name = layer["name"]
        if name in layers or not re.fullmatch(r"[\w-]+", name):
            raise ValueError(f"Duplicate or invalid Godot layer name: {name}")
        coordinates = set()
        data = bytearray(2)
        for t in layer["tiles"]:
            x, y = t["x"] // tile_size - x0, t["y"] // tile_size - y0
            coordinates.add((x,y))
            key = (t["spriteSheetId"], int(t["id"]), t.get("scaleX",1), t.get("scaleY",1), bool(layer.get("collider")))
            if key not in atlas_index:
                sheet = sheets[key[0]]
                columns = sheet.width // tile_size
                tx, ty = key[1] % columns * tile_size, key[1] // columns * tile_size
                if ty < 0 or ty+tile_size > sheet.height:
                    raise ValueError(f"Invalid tile {key[:2]}")
                im = sheet.crop((tx, ty, tx+tile_size, ty+tile_size))
                if key[2] < 0:
                    im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if key[3] < 0:
                    im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                tile = (im.tobytes(), key[4])
                if existing_resource and tile in by_pixels:
                    atlas_index[key] = by_pixels[tile]
                else:
                    atlas_index[key] = len(textures)
                    if existing_resource:
                        by_pixels[tile] = len(textures)
                    textures.append((im, key[4]))
            idx = atlas_index[key]
            data.extend(struct.pack("<6h", x,y,0,idx%atlas_columns,idx//atlas_columns,0))
            if picture is not None:
                picture.alpha_composite(textures[idx][0], (x*tile_size,y*tile_size))
        layers[name] = coordinates
        encoded.extend([f'[node name="{name}" type="TileMapLayer" parent="."]',
                        'tile_set = ExtResource("tiles")',
                        f'collision_enabled = {str(bool(layer.get("collider"))).lower()}',
                        f'tile_map_data = PackedByteArray("{base64.b64encode(data).decode()}")', ''])
    rows = (len(textures) + atlas_columns - 1) // atlas_columns
    atlas = Image.new("RGBA", (tile_size * atlas_columns, tile_size * rows))
    definitions = []
    radius = tile_size / 2
    poly = ", ".join(f"{n:g}" for n in (-radius,-radius,radius,-radius,radius,radius,-radius,radius))
    for idx,(im,solid) in enumerate(textures):
        ax, ay = idx % atlas_columns, idx // atlas_columns
        atlas.paste(im, (ax*tile_size,ay*tile_size))
        if idx < existing_count:
            continue
        definitions.append(f"{ax}:{ay}/0 = 0")
        if solid:
            definitions.append(f"{ax}:{ay}/0/physics_layer_0/polygon_0/points = PackedVector2Array({poly})")
    atlas.save(directory/"tileset.png")
    resource = ['[gd_resource type="TileSet" load_steps=3 format=3]', '',
                '[ext_resource type="Texture2D" path="tileset.png" id="atlas"]', '',
                '[sub_resource type="TileSetAtlasSource" id="source"]', 'texture = ExtResource("atlas")',
                f'texture_region_size = Vector2i({tile_size}, {tile_size})', *definitions, '',
                '[resource]', f'tile_size = Vector2i({tile_size}, {tile_size})',
                'physics_layer_0/collision_layer = 1', 'physics_layer_0/collision_mask = 2',
                'sources/0 = SubResource("source")', '']
    if existing_resource:
        if definitions:
            head, settings = existing_resource.split("\n[resource]", 1)
            updated = head.rstrip() + "\n" + "\n".join(definitions) + "\n\n[resource]" + settings
            (directory / "tileset.tres").write_text(updated, encoding="utf-8")
    else:
        (directory/"tileset.tres").write_text("\n".join(resource), encoding="utf-8")
    scene = ['[gd_scene load_steps=2 format=3]', '',
             '[ext_resource type="TileSet" path="tileset.tres" id="tiles"]', '',
             '[node name="Map" type="Node2D"]', '', *encoded]
    (directory/"Map.tscn").write_text("\n".join(scene), encoding="utf-8")
    manifest.update(size=[w,h], tile_size=tile_size)
    save(directory/"manifest.json", manifest)
    write_navigation(directory, manifest, layers)
    if picture is not None:
        bg = Image.new("RGBA", picture.size, "#222b35")
        bg.alpha_composite(picture)
        bg.convert("RGB").save(preview)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    build(args.source, args.directory, args.preview)
