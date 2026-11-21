class_name PlayerAvatar
extends Node2D
## 玩家在主世界中的可行走实体；坐标沿用 NPC 的楼层网格。

var map_id := Game.FLOOR3
var step_delay := 0.0
var last_walk_msec := -1000
var sprite: AnimatedSprite2D
var movement := preload("res://scripts/movement_keys.gd").new()

func _ready() -> void:
	add_child(movement)
	sprite = AnimatedSprite2D.new()
	sprite.sprite_frames = preload("res://scenes/char_1.tres")
	sprite.modulate = Color("#ffd86b")
	sprite.position = Vector2(0, -8)
	add_child(sprite)
	sprite.play("idle")
	var label := Label.new()
	label.text = "你"
	label.position = Vector2(-8, -38)
	label.add_theme_color_override("font_color", Color("#ffe6a0"))
	label.add_theme_color_override("font_outline_color", Color.BLACK)
	label.add_theme_constant_override("outline_size", 3)
	add_child(label)

func walk(delta: float) -> bool:
	step_delay = maxf(0.0, step_delay - delta)
	if step_delay > 0.0:return false
	var direction := movement.direction()
	if direction == Vector2i.ZERO:
		sprite.play("idle")
		return false
	step_delay = 0.12
	var next_cell := Game.cell_of(global_position, map_id) + direction
	if Game.is_blocked(next_cell, map_id):
		sprite.play("idle")
		return false
	global_position = Game.world_of(next_cell, map_id)
	last_walk_msec = Time.get_ticks_msec()
	sprite.play(movement.ANIMATIONS[direction])
	return true
