"""Rebuild a Godot floor from a Sprite Fusion project and its gameplay manifest.

    python build_floor.py SOURCE.json FLOOR_DIR [--preview floor.png]

The manifest in FLOOR_DIR stores unique object IDs and coordinates in the
exported map's grid. Sprite Fusion stores placements in source pixel space.
"""
import argparse
import base64
import hashlib
import io
import json
import re
import struct
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def tile_cells(body):
    raw = re.search(r'^tile_map_data\s*=\s*PackedByteArray\(([^)]*)\)', body, re.M)
    if not raw or not raw[1].strip():
        return set()
    payload = raw[1].strip()
    if payload.startswith('"'):
        data = base64.b64decode(payload.strip('"'))
    else:
        data = bytes(int(part) for part in payload.split(','))
    return {(x, y) for x, y, *_ in struct.iter_unpack("<6h", data[2:])}


def read_godot_layers(directory):
    """Read the TileMapLayer children actually used by Map, in drawing order."""
    scene = (directory / "Map.tscn").read_text(encoding="utf-8-sig")
    packed = {}
    for header in re.findall(r'^\[ext_resource (.*)\]$', scene, re.M):
        fields = dict(re.findall(r'(\w+)="([^"]+)"', header))
        if fields.get("type") == "PackedScene":
            packed[fields["id"]] = fields["path"]
    nodes = re.compile(r'^\[node ([^\n]*)\]\n([^\[]*)', re.M)
    layers = {}
    for header, body in nodes.findall(scene):
        fields = dict(re.findall(r'(\w+)="([^"]*)"', header))
        if fields.get("parent") != ".":
            continue
        if fields.get("type") == "TileMapLayer":
            layers[fields["name"]] = tile_cells(body)
            continue
        instance = re.search(r'instance=ExtResource\("([^"]+)"\)', header)
        if not instance:
            continue
        path = packed[instance[1]]
        if path.startswith("res://"):
            project = next(p for p in (directory, *directory.parents) if (p / "project.godot").exists())
            layer_file = project / path[6:]
        else:
            layer_file = directory / path
        child = layer_file.read_text(encoding="utf-8-sig")
        root = nodes.search(child)
        if root and 'type="TileMapLayer"' in root[1]:
            layers[fields["name"]] = tile_cells(body if "tile_map_data =" in body else root[2])
    return layers


def layer_signature(name, content):
    """Identify untouched generated scenes when a source layer is removed."""
    digest = hashlib.sha256((name + "\n" + content).encode("utf-8")).hexdigest()
    return "; build_floor.py " + digest


def generated_layer_is_unchanged(child):
    if child.is_symlink() or not child.is_file():
        return False
    try:
        text = child.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return False
    content, separator, signature = text.rstrip("\n").rpartition("\n")
    return separator and signature == layer_signature(child.stem, content + "\n")


def validate_layer_paths(directory, layers):
    layer_dir = directory / "layers"
    names = set()
    for layer in layers:
        name = layer["name"]
        if not re.fullmatch(r"[\w-]+", name) or name.casefold() in names:
            raise ValueError(f"Duplicate or invalid layer filename: {name}")
        names.add(name.casefold())
        child = layer_dir / (name + ".tscn")
        if child.exists() and not generated_layer_is_unchanged(child):
            raise ValueError(f"Cannot overwrite a hand-edited layer scene: {child}")


def write_layer_scenes(directory, encoded):
    layer_dir = directory / "layers"
    layer_dir.mkdir(exist_ok=True)
    scene = [f'[gd_scene load_steps={len(encoded)+1} format=3]', '']
    instances = ['[node name="Map" type="Node2D"]', '']
    for name, (data, solid) in encoded.items():
        content = '\n'.join([
            '[gd_scene load_steps=2 format=3]', '',
            '[ext_resource type="TileSet" path="../tileset.tres" id="tiles"]', '',
            f'[node name="{name}" type="TileMapLayer"]', 'tile_set = ExtResource("tiles")',
            f'collision_enabled = {str(solid).lower()}', f'tile_map_data = PackedByteArray("{data}")', ''
        ])
        child = layer_dir / (name + ".tscn")
        child.write_text(content + layer_signature(name, content) + "\n", encoding="utf-8")
        scene.append(f'[ext_resource type="PackedScene" path="layers/{name}.tscn" id="{name}"]')
        instances.extend([f'[node name="{name}" parent="." instance=ExtResource("{name}")]', ''])
    (directory / "Map.tscn").write_text('\n'.join([*scene, '', *instances]), encoding="utf-8")
    for child in layer_dir.glob("*.tscn"):
        if child.stem not in encoded and generated_layer_is_unchanged(child):
            child.unlink()


def navigation_grid(manifest, layers):
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
        rid = room["id"]
        layer_name = room.get("layer")
        if layer_name not in layers:
            raise ValueError(f"Room {rid} references missing floor layer {layer_name}")
        all_cells = layers[layer_name]
        walk = all_cells - blocked
        if not walk:
            raise ValueError(f"Room {rid} has no walkable cells")
        spawn = room.get("spawn")
        if spawn is not None and tuple(spawn) not in walk:
            raise ValueError(f"Spawn {spawn} for {rid} must be on its own walkable floor")
        for x, y in all_cells:
            if not (0 <= x < w and 0 <= y < h):
                raise ValueError(f"Room {rid} has a cell outside the map: {(x,y)}")
            if room_grid[y][x]:
                previous = manifest["rooms"][room_grid[y][x] - 1]["id"]
                raise ValueError(f"Room floors {previous} and {rid} overlap at {(x,y)}")
            room_grid[y][x] = index
    unlisted = {n for n in layers if n.startswith("room_")} - {r["layer"] for r in manifest["rooms"]}
    if unlisted:
        raise ValueError(f"Room floor layers missing from manifest: {sorted(unlisted)}")
    for x, y in blocked:
        if 0 <= x < w and 0 <= y < h:
            room_grid[y][x] = 0
    # A doorway may be drawn over an intentional gap between two floor layers.
    for obj in manifest["objects"]:
        if obj["type"] == "door":
            room_no = next(i for i,r in enumerate(manifest["rooms"],1) if r["id"] == obj["room"])
            for x,y in obj.get("cells", [obj["cell"]]):
                if not room_grid[y][x]:
                    room_grid[y][x] = room_no
    return dict(tile_size=manifest["tile_size"], origin=[0, 0], size=[w,h],
                room_grid=room_grid, door_cells=sorted(doors),
                obstacle=["".join("0" if cell else "1" for cell in row) for row in room_grid])


def write_navigation(target, manifest, layers):
    grid = navigation_grid(manifest, layers)
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
    validate_layer_paths(directory, project["layers"])
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
    layers, encoded = {}, {}
    picture = Image.new("RGBA", (w * tile_size, h * tile_size)) if preview else None
    for layer in reversed(project["layers"]):
        name = layer["name"]
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
        encoded[name] = base64.b64encode(data).decode(), bool(layer.get("collider"))
    manifest.update(size=[w,h], tile_size=tile_size)
    grid = navigation_grid(manifest, layers)
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
    write_layer_scenes(directory, encoded)
    save(directory/"manifest.json", manifest)
    save(directory/"grid.json", grid)
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
