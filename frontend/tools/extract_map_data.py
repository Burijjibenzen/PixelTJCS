# -*- coding: utf-8 -*-
"""地图数据提取工具: Godot 4.3+ 分层瓦片场景 → 寻路网格 + 房间划分

用法:
    D:/py3.11/python.exe -X utf8 extract_map_data.py <地图目录> <输出目录>

输入: <地图目录>/ 下绘图工具导出的场景
    ground_6.tscn (地面, 必需), wall_3.tscn (墙, 必需),
    zhuangshi_2 / zhuangzishangmian_1 / guizi_0 / louti_4 / ditan_N (可选)

输出(写入 <输出目录>):
    floor3_grid.json     寻路网格: 障碍矩阵 + 房间id矩阵 + 门格 (Godot AStarGrid2D 用)
    rooms.json           房间元数据: id/名称/类型/导航点/门 (视野系统数据源, 人工可改)
    _region_preview.txt  区块预览(调试用, 可删)

房间判定算法:
    1. 障碍 = 带碰撞瓦片 ∪ 桌上物层 ∪ 柜子层
    2. 可走 = 有地面 且 非障碍
    3. 门格 = 可走格 且 左右皆障碍 或 上下皆障碍
    4. 门格视为墙做洪水填充(4向), 连通块 = 区域
    5. 类型猜测: 含楼梯→stairwell; 细长→corridor; 否则 room
    名称/类型在 rooms.json 里人工修改, Godot 与后端只认 id。
画新楼层后重跑本脚本即可刷新网格与 rooms.json。
"""
import json
import os
import re
import struct
import sys
from collections import deque

TILE = 16
FURNITURE_LAYERS = ["zhuangzishangmian_1", "guizi_0"]  # 家具层: 全部视为障碍
STAIR_LAYER = "louti_4"
GROUND_LAYER = "ground_6"
WALL_LAYER = "wall_3"
MIN_REGION_TILES = 8  # 小于该格数的区域视为碎片丢弃


def read_tilemap_layer(path):
    """解析 .tscn 的 tile_map_data PackedByteArray → {(x,y): (atlas_x, atlas_y)}
    格式: uint16 版本头, 之后每 cell 12 字节 (小端 int16 ×6)"""
    text = open(path, encoding="utf-8").read()
    m = re.search(r"tile_map_data = PackedByteArray\(([\d,\s]+)\)", text)
    if not m:
        return {}
    raw = bytes(int(v) for v in m.group(1).split(","))
    cells = {}
    for off in range(2, len(raw) - 11, 12):
        x, y, _src, ax, ay, _alt = struct.unpack("<hhhhhh", raw[off:off + 12])
        cells[(x, y)] = (ax, ay)
    return cells


def read_collision_tiles(path):
    """解析 tileset.tres → 带物理碰撞的 {(atlas_x, atlas_y)} 集合"""
    text = open(path, encoding="utf-8").read()
    return {(int(m.group(1)), int(m.group(2)))
            for m in re.finditer(r"(\d+):(\d+)/0/physics_layer_0", text)}


def load_overrides(path):
    """读取 room_overrides.json (可选, 人工维护):
    extra_walls: [[x,y],...] 视作墙的格子 (封闭开放门洞, 留 2 格当门)
    renames: {"R01": {"name": "走廊", "type": "corridor"}, ...} 命名/类型覆盖"""
    if not os.path.exists(path):
        return {"extra_walls": [], "renames": {}}
    with open(path, encoding="utf-8") as f:
        ov = json.load(f)
    ov.setdefault("extra_walls", [])
    ov.setdefault("renames", {})
    return ov


def main(src_dir, out_dir):
    overrides = load_overrides(os.path.join(src_dir, "room_overrides.json"))
    layers = {}
    for f in os.listdir(src_dir):
        if f.endswith(".tscn") and re.match(r"^[a-z]+_\d+\.tscn$", f):
            layers[f[:-5]] = read_tilemap_layer(os.path.join(src_dir, f))
    if GROUND_LAYER not in layers or WALL_LAYER not in layers:
        sys.exit(f"缺少必需层 {GROUND_LAYER}/{WALL_LAYER}, 现有: {list(layers)}")
    collision_tiles = read_collision_tiles(os.path.join(src_dir, "tileset.tres"))

    # ---- 障碍/可走矩阵 ----
    # 墙 = wall_3 图层本身 (该层即墙体, 不靠图集猜测);
    # 家具 = 桌上物/柜子图层; 其余装饰/地毯/楼梯均可走
    all_pos = set()
    for cells in layers.values():
        all_pos |= set(cells)
    x0 = min(p[0] for p in all_pos); x1 = max(p[0] for p in all_pos)
    y0 = min(p[1] for p in all_pos); y1 = max(p[1] for p in all_pos)
    W, H = x1 - x0 + 1, y1 - y0 + 1

    def layer_at(name):
        g = [[False] * W for _ in range(H)]
        for (x, y) in layers.get(name, {}):
            g[y - y0][x - x0] = True
        return g

    wall_g = layer_at(WALL_LAYER)
    obst_layers = [layer_at(n) for n in FURNITURE_LAYERS]
    obstacle = [[True] * W for _ in range(H)]   # 图外/无地面默认障碍
    for (x, y) in layers[GROUND_LAYER]:
        obstacle[y - y0][x - x0] = False
    for g in [wall_g] + obst_layers:
        for yy in range(H):
            for xx in range(W):
                if g[yy][xx]:
                    obstacle[yy][xx] = True
    is_stair = layer_at(STAIR_LAYER)
    # 应用人工分割墙
    for (x, y) in overrides["extra_walls"]:
        if x0 <= x <= x1 and y0 <= y <= y1:
            gx, gy = x - x0, y - y0
            obstacle[gy][gx] = True
            wall_g[gy][gx] = True
    walkable = [[not obstacle[yy][xx] for xx in range(W)] for yy in range(H)]

    # ---- 门检测: wall_3 墙线上宽 1~3 的缺口 (桌椅缝隙两侧非墙, 不会误封) ----
    doors = set()

    def wall(xx, yy):
        return not (0 <= xx < W and 0 <= yy < H) or wall_g[yy][xx]

    def wgap(x0_, x1_, y):  # 水平墙线上 [x0_,x1_] 行 y 的缺口 (宽≤3 才是门)
        if x1_ - x0_ + 1 > 3:
            return
        for xx in range(x0_, x1_ + 1):
            if not walkable[y][xx]:
                return
        if wall(x0_ - 1, y) and wall(x1_ + 1, y):
            doors.update((xx, y) for xx in range(x0_, x1_ + 1))

    def vgap(x, y0_, y1_):
        if y1_ - y0_ + 1 > 3:
            return
        for yy in range(y0_, y1_ + 1):
            if not walkable[yy][x]:
                return
        if wall(x, y0_ - 1) and wall(x, y1_ + 1):
            doors.update((x, yy) for yy in range(y0_, y1_ + 1))

    # 逐行/逐列扫描 "墙 | 可走段(宽≤3) | 墙" 模式
    for yy in range(H):
        xx = 0
        while xx < W:
            if not wall_g[yy][xx] and walkable[yy][xx]:
                s = xx
                while xx < W and walkable[yy][xx] and not wall_g[yy][xx]:
                    xx += 1
                wgap(s, xx - 1, yy)            # 左右端外是墙 → 整段为门
            else:
                xx += 1
    for xx in range(W):
        yy = 0
        while yy < H:
            if not wall_g[yy][xx] and walkable[yy][xx]:
                s = yy
                while yy < H and walkable[yy][xx] and not wall_g[yy][xx]:
                    yy += 1
                vgap(xx, s, yy - 1)
            else:
                yy += 1

    region = [[0] * W for _ in range(H)]
    raw_regions = {}                                # rid -> cells
    rid = 0
    for sy in range(H):
        for sx in range(W):
            if not walkable[sy][sx] or region[sy][sx] or (sx, sy) in doors:
                continue
            rid += 1
            q = deque([(sx, sy)])
            region[sy][sx] = rid
            cells = []
            while q:
                cx, cy = q.popleft()
                cells.append((cx, cy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (walkable[ny][nx] and not region[ny][nx]
                            and (nx, ny) not in doors):
                        region[ny][nx] = rid
                        q.append((nx, ny))
            raw_regions[rid] = cells
    big = {k: v for k, v in raw_regions.items() if len(v) >= MIN_REGION_TILES}

    # ---- 区域元数据 + 类型猜测 ----
    order = sorted(big, key=lambda k: -len(big[k]))     # 面积降序 → R01..
    meta = []
    for i, old_rid in enumerate(order):
        cells = big[old_rid]
        xs = [c[0] for c in cells]; ys = [c[1] for c in cells]
        bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        aspect = max(bw, bh) / max(1, min(bw, bh))
        stairs = sum(1 for (gx, gy) in cells if is_stair[gy][gx])
        cxg = sum(xs) / len(xs); cyg = sum(ys) / len(ys)
        nav = min(cells, key=lambda c: (c[0] - cxg) ** 2 + (c[1] - cyg) ** 2)
        if stairs > 0:
            rtype = "stairwell"
        elif aspect > 3.5 or (len(cells) > 250 and aspect > 2.5):
            rtype = "corridor"
        else:
            rtype = "room"
        meta.append({
            "id": f"R{i + 1:02d}", "name": f"区域{i + 1:02d}", "type": rtype,
            "area_tiles": len(cells),
            "bbox": [min(xs) + x0, min(ys) + y0, max(xs) + x0, max(ys) + y0],
            "aspect": round(aspect, 1), "stair_tiles": stairs,
            "nav_point": [nav[0] + x0, nav[1] + y0],
            "door_cells": [[gx + x0, gy + y0] for (gx, gy) in cells
                           if any((gx + dx, gy + dy) in doors
                                  for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))][:40],
        })

    # ---- 人工命名/类型覆盖 ----
    for m in meta:
        ov = overrides["renames"].get(m["id"], {})
        if "name" in ov:
            m["name"] = ov["name"]
        if "type" in ov:
            m["type"] = ov["type"]

    # ---- 输出 ----
    os.makedirs(out_dir, exist_ok=True)
    new_num = {old: i + 1 for i, old in enumerate(order)}
    room_grid = [[0] * W for _ in range(H)]
    for old_rid, cells in big.items():
        for (gx, gy) in cells:
            room_grid[gy][gx] = new_num[old_rid]
    # 孤立可走格(门格/碎片) 从邻接区域继承 id (BFS 扩散), 保证 room_at 全覆盖
    q = deque((xx, yy) for yy in range(H) for xx in range(W) if room_grid[yy][xx])
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (0 <= nx < W and 0 <= ny < H and walkable[ny][nx]
                    and room_grid[ny][nx] == 0):
                room_grid[ny][nx] = room_grid[cy][cx]
                q.append((nx, ny))

    grid = {
        "tile_size": TILE, "origin": [x0, y0], "size": [W, H],
        "obstacle": ["".join("1" if obstacle[y][x] else "0" for x in range(W))
                     for y in range(H)],
        "room_grid": room_grid,
        "door_cells": [[d[0] + x0, d[1] + y0] for d in sorted(doors)],
    }
    with open(os.path.join(out_dir, "floor3_grid.json"), "w", encoding="utf-8") as f:
        json.dump(grid, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(out_dir, "rooms.json"), "w", encoding="utf-8") as f:
        json.dump({"tile_size": TILE, "map_name": "jishilou_floor3",
                   "regions": meta}, f, ensure_ascii=False, indent=1)

    # 预览图 (每区域一个字符)
    CH = "123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ch_of = {new_num[old]: CH[i % len(CH)] for i, old in enumerate(order)}
    lines = [f"regions={len(meta)} doors={len(doors)}"]
    for yy in range(H):
        row = ""
        for xx in range(W):
            if room_grid[yy][xx]:
                row += ch_of[room_grid[yy][xx]]
            elif obstacle[yy][xx]:
                row += "#"
            else:
                row += "?"
        lines.append(row)
    lines.append("")
    for m in meta:
        lines.append(f"{m['id']} {m['type']:<9} {m['area_tiles']:>4}格 "
                     f"bbox={m['bbox']} 长宽比={m['aspect']} "
                     f"楼梯格={m['stair_tiles']} nav={m['nav_point']}")
    with open(os.path.join(out_dir, "_region_preview.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"EXTRACT_DONE regions={len(meta)} doors={len(doors)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
