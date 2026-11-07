# -*- coding: utf-8 -*-
"""生成 character_1~4.tres (SpriteFrames): 从 AI-Town 角色贴图切动画帧
帧布局(与 hello-agents AI-Town main.tscn 一致, 48x70/帧):
  idle 14帧 y=1086 x=2016+48i ; walk_right x=0 walk_up x=288 walk_left x=576
  walk_down x=864 (均 y=792, 6帧) ; speed 5fps loop
用法: D:/py3.11/python.exe -X utf8 gen_char_frames.py <assets目录> <输出目录>
"""
import os
import struct
import sys

FRAME_W, FRAME_H = 48, 70
ANIMS = [
    ("idle", [(2016 + 48 * i, 1086) for i in range(14)]),
    ("walk_down", [(864 + 48 * i, 792) for i in range(6)]),
    ("walk_left", [(576 + 48 * i, 792) for i in range(6)]),
    ("walk_right", [(0 + 48 * i, 792) for i in range(6)]),
    ("walk_up", [(288 + 48 * i, 792) for i in range(6)]),
]


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def gen(src, out):
    os.makedirs(out, exist_ok=True)
    for ci in range(1, 5):
        tex = f"res://assets/characters/character_{ci}.png"
        p = os.path.join(src, f"character_{ci}.png")
        size = png_size(p)
        need_w = 2016 + 48 * 13 + FRAME_W
        need_h = 1086 + FRAME_H
        if size is None or size[0] < need_w or size[1] < need_h:
            print(f"WARN character_{ci}.png 尺寸={size} 小于帧布局所需 "
                  f"({need_w}x{need_h}), 越界帧将被裁剪")
        lines = []
        subs = []
        n = 0
        anims_json = []
        for aname, frames in ANIMS:
            refs = []
            for (x, y) in frames:
                sid = f"at_{ci}_{n}"
                subs.append(f'[sub_resource type="AtlasTexture" id="{sid}"]\n'
                            f'atlas = ExtResource("1")\n'
                            f'region = Rect2({x}, {y}, {FRAME_W}, {FRAME_H})\n')
                refs.append(sid)
                n += 1
            frames_json = ", ".join(
                '{{"duration": 1.0, "texture": SubResource("{0}")}}'.format(r)
                for r in refs)
            anims_json.append(
                '{{"frames": [{0}], "loop": true, "name": &"{1}", '
                '"speed": 5.0}}'.format(frames_json, aname))
        lines.append('[gd_resource type="SpriteFrames" '
                     f'load_steps={n + 2} format=3]\n')
        lines.append(f'[ext_resource type="Texture2D" path="{tex}" id="1"]\n\n')
        lines.append("\n".join(subs))
        lines.append("\n[resource]\nanimations = [")
        lines.append(", ".join(anims_json))
        lines.append("]\n")
        dst = os.path.join(out, f"char_{ci}.tres")
        with open(dst, "w", encoding="utf-8") as f:
            f.write("".join(lines))
        print(f"OK {dst} ({n} frames)")


if __name__ == "__main__":
    gen(sys.argv[1], sys.argv[2])
